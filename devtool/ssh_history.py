from __future__ import annotations

"""Canonical SSH run/history/output projections.

SSH-02 intentionally does not create an SSH-only database. Durable bytes and
phase metadata live in the remote run directory, while typed EXO/RunStore
execution events remain the authoritative local execution model. This module is
only a projection/recovery layer over those sources.
"""

from collections import defaultdict
import json
import os
from pathlib import Path
import shlex
import sys
import time
from typing import Any, Mapping, TextIO

from .core.events import ProcessOutputReceived, publish_event
from .ssh_resilience import _run_remote, _select_records, remote_phase_state_paths, runs_payload
from .ssh_tmux_runner import SSHRunnerConfig


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _selected_record(
    config: SSHRunnerConfig,
    *,
    run_id: str = "",
    session: str = "",
    latest: bool = False,
) -> dict[str, Any]:
    payload = runs_payload(config)
    records = [dict(item) for item in payload.get("records", []) if isinstance(item, Mapping)]
    if run_id or session:
        matches = _select_records(payload, run_id=run_id, session=session)
    elif latest or records:
        matches = records[:1]
    else:
        matches = []
    if not matches:
        raise ValueError("No SSH worker run/session matched the output request")
    return dict(matches[0])


def _stream_path(config: SSHRunnerConfig, record: Mapping[str, Any], stream: str) -> str:
    paths = remote_phase_state_paths(config, str(record.get("run_id") or ""), str(record.get("session") or ""))
    return paths["stderr" if stream == "stderr" else "stdout"]


def _read_stream(config: SSHRunnerConfig, record: Mapping[str, Any], stream: str, *, offset: int = 0) -> str:
    path = _stream_path(config, record, stream)
    # tail -c is available on Termux/coreutils and normal Linux workers and lets
    # reconnecting followers resume at a byte offset without replaying output.
    start = max(0, int(offset)) + 1
    script = f"set +e; p={shlex.quote(path)}; [ -f \"$p\" ] || exit 0; tail -c +{start} -- \"$p\" 2>/dev/null"
    completed = _run_remote(config, script, timeout=30.0)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH persisted {stream} read failed: {detail or completed.returncode}")
    return completed.stdout or ""


def output_payload(
    config: SSHRunnerConfig,
    *,
    run_id: str = "",
    session: str = "",
    latest: bool = False,
) -> dict[str, Any]:
    record = _selected_record(config, run_id=run_id, session=session, latest=latest)
    stdout = _read_stream(config, record, "stdout")
    stderr = _read_stream(config, record, "stderr")
    return {
        "schema_version": 1,
        "kind": "ssh-output",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "run_id": str(record.get("run_id") or ""),
        "session": str(record.get("session") or ""),
        "state": str(record.get("state") or "unknown"),
        "status": record.get("status"),
        "active": bool(record.get("active")),
        "output_state": str(record.get("output_state") or "unavailable"),
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
        "record": record,
    }


def _refresh_record(config: SSHRunnerConfig, record: Mapping[str, Any]) -> dict[str, Any]:
    payload = runs_payload(config)
    matches = _select_records(
        payload,
        run_id=str(record.get("run_id") or ""),
        session=str(record.get("session") or ""),
    )
    return dict(matches[0]) if matches else dict(record)


def follow_output(
    config: SSHRunnerConfig,
    *,
    run_id: str = "",
    session: str = "",
    latest: bool = False,
    stdout_writer: TextIO | None = None,
    stderr_writer: TextIO | None = None,
    poll_interval: float = 0.25,
    max_idle_seconds: float = 0.0,
    reconnect_grace_seconds: float = 30.0,
) -> dict[str, Any]:
    """Follow persisted stdout/stderr without attaching to tmux.

    Offsets are local to the client invocation, so a reconnect starts from the
    persisted beginning by default and never duplicates bytes within one follow
    session. The loop stops as soon as the canonical phase record is terminal.
    """
    out = stdout_writer or sys.stdout
    err = stderr_writer or sys.stderr
    record = _selected_record(config, run_id=run_id, session=session, latest=latest)
    stdout_offset = 0
    stderr_offset = 0
    last_activity = time.monotonic()
    process_id = "ssh:" + str(record.get("session") or "phase")

    transport_lost_at: float | None = None
    reconnects = 0
    while True:
        try:
            stdout_chunk = _read_stream(config, record, "stdout", offset=stdout_offset)
            stderr_chunk = _read_stream(config, record, "stderr", offset=stderr_offset)
            transport_lost_at = None
        except (OSError, ValueError):
            now = time.monotonic()
            transport_lost_at = transport_lost_at or now
            if reconnect_grace_seconds <= 0 or now - transport_lost_at >= reconnect_grace_seconds:
                raise
            reconnects += 1
            time.sleep(max(0.05, float(poll_interval)))
            continue
        if stdout_chunk:
            out.write(stdout_chunk)
            out.flush()
            stdout_offset += len(stdout_chunk.encode("utf-8", errors="replace"))
            last_activity = time.monotonic()
            publish_event(ProcessOutputReceived(
                run_id=str(record.get("run_id") or ""),
                node_id=str(record.get("phase_node_id") or record.get("session") or "ssh-output"),
                process_id=process_id,
                stream="stdout",
                text=stdout_chunk,
                source="ssh-history",
            ))
        if stderr_chunk:
            err.write(stderr_chunk)
            err.flush()
            stderr_offset += len(stderr_chunk.encode("utf-8", errors="replace"))
            last_activity = time.monotonic()
            publish_event(ProcessOutputReceived(
                run_id=str(record.get("run_id") or ""),
                node_id=str(record.get("phase_node_id") or record.get("session") or "ssh-output"),
                process_id=process_id,
                stream="stderr",
                text=stderr_chunk,
                source="ssh-history",
            ))

        record = _refresh_record(config, record)
        terminal = not bool(record.get("active")) and str(record.get("state") or "") in {
            "completed", "cancelled", "interrupted", "stale",
        }
        if terminal:
            # One final read closes the race between the last file write and the
            # state/status rename performed by the remote phase wrapper.
            final_out = _read_stream(config, record, "stdout", offset=stdout_offset)
            final_err = _read_stream(config, record, "stderr", offset=stderr_offset)
            if final_out:
                out.write(final_out); out.flush()
                stdout_offset += len(final_out.encode("utf-8", errors="replace"))
            if final_err:
                err.write(final_err); err.flush()
                stderr_offset += len(final_err.encode("utf-8", errors="replace"))
            break
        if max_idle_seconds > 0 and time.monotonic() - last_activity >= max_idle_seconds:
            break
        time.sleep(max(0.05, float(poll_interval)))

    return {
        "schema_version": 1,
        "kind": "ssh-output-follow",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "run_id": str(record.get("run_id") or ""),
        "session": str(record.get("session") or ""),
        "state": str(record.get("state") or "unknown"),
        "status": record.get("status"),
        "active": bool(record.get("active")),
        "output_state": str(record.get("output_state") or "unavailable"),
        "stdout_bytes": stdout_offset,
        "stderr_bytes": stderr_offset,
        "reconnects": reconnects,
        "record": record,
    }


def _local_return_state(repo_root: str | Path | None, *, run_id: str, session: str = "") -> dict[str, Any]:
    if not repo_root or not run_id:
        return {}
    root = Path(repo_root).expanduser().resolve() / ".devtool" / "ssh-return"
    if not root.is_dir():
        return {}
    from .ssh_returns import load_return_state
    candidates = sorted(root.glob("*/*/manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for manifest_path in candidates:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(manifest.get("run_id") or "") != str(run_id):
            continue
        if session and str(manifest.get("session") or "") != str(session):
            continue
        return load_return_state({
            "ssh_return_manifest": str(manifest_path),
            "ssh_return_status_file": str(manifest_path.parent / "transfer.status"),
            "ssh_return_root": str(manifest_path.parent),
            "ssh_return_repo_root": str(manifest.get("local_return_repo_root") or manifest_path.parent / "repo"),
            "ssh_return_project_root": str(manifest.get("local_return_project_root") or ""),
            "ssh_return_remote_root": str(manifest.get("remote_root") or ""),
            "ssh_source_sync_id": str(manifest.get("source_sync_id") or ""),
        })
    return {}


def _runstore_projection(repo_root: str | Path | None, run_id: str) -> dict[str, Any]:
    if not repo_root or not run_id:
        return {}
    from .test_runstore import load_test_run
    payload = load_test_run(run_id)
    if not isinstance(payload, dict):
        return {}
    expected_repo = str(Path(repo_root).expanduser().resolve())
    if str(payload.get("repository") or "") != expected_repo:
        return {}
    return {
        "summary": dict(payload.get("summary") or {}),
        "artifacts": list(payload.get("artifacts") or []),
        "build_artifacts": list(payload.get("build_artifacts") or []),
        "logs": list(payload.get("logs") or []),
        "operations": list(payload.get("operations") or []),
        "comparison": dict(payload.get("comparison") or {}),
    }


def sessions_payload(config: SSHRunnerConfig, *, repo_root: str | Path | None = None) -> dict[str, Any]:
    source = runs_payload(config)
    sessions = []
    for item in source.get("records", []):
        rid = str(item.get("run_id") or "")
        sess = str(item.get("session") or "")
        returned = _local_return_state(repo_root, run_id=rid, session=sess)
        sessions.append({
            "name": sess,
            "run_id": rid,
            "phase": str(item.get("phase") or ""),
            "state": str(item.get("state") or "unknown"),
            "active": bool(item.get("active")),
            "live": bool(item.get("active")),
            "dead": not bool(item.get("active")),
            "status": item.get("status"),
            "workspace_id": str(item.get("workspace_id") or ""),
            "workspace_path": str(item.get("workspace_path") or ""),
            "target": str(item.get("target") or item.get("federation_child_target") or ""),
            "output_state": str(item.get("output_state") or "unavailable"),
            "started_unix": _safe_int(item.get("started_unix")),
            "finished_unix": _safe_int(item.get("finished_unix")),
            "duration_seconds": _safe_int(item.get("duration_seconds")),
            "tmux_window": str(item.get("tmux_window") or "0"),
            "tmux_pane": str(item.get("tmux_pane") or "0"),
            "return_state": str(returned.get("state") or "unavailable"),
            "return_recoverable": bool(returned.get("recoverable")),
        })
    return {
        "schema_version": 2,
        "kind": "ssh-sessions",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "sessions": sessions,
        "historical": True,
        "active_count": sum(1 for item in sessions if item["active"]),
    }


def grouped_runs_payload(config: SSHRunnerConfig, *, repo_root: str | Path | None = None) -> dict[str, Any]:
    source = runs_payload(config)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in source.get("records", []):
        grouped[str(item.get("run_id") or "")].append(dict(item))
    runs: list[dict[str, Any]] = []
    for run_id, phases in grouped.items():
        phases.sort(key=lambda item: (_safe_int(item.get("started_unix")), str(item.get("session") or "")))
        active = any(bool(item.get("active")) for item in phases)
        states = {str(item.get("state") or "unknown") for item in phases}
        statuses = [item.get("status") for item in phases if item.get("status") is not None]
        status = next((int(value) for value in statuses if int(value) != 0), (int(statuses[-1]) if statuses else None))
        if active:
            state = "running"
        elif "interrupted" in states or "stale" in states:
            state = "interrupted"
        elif "cancelled" in states:
            state = "cancelled"
        elif phases and all(str(item.get("state") or "") == "completed" for item in phases):
            state = "completed"
        else:
            state = "partial"
        first = phases[0] if phases else {}
        last = phases[-1] if phases else {}
        return_states = [_local_return_state(repo_root, run_id=run_id, session=str(item.get("session") or "")) for item in phases]
        return_states = [item for item in return_states if item]
        runstore = _runstore_projection(repo_root, run_id)
        return_state = (
            "recoverable" if any(bool(item.get("recoverable")) for item in return_states)
            else "complete" if return_states and all(str(item.get("state") or "") == "complete" for item in return_states)
            else "unavailable"
        )
        recovery_hint = (
            f"devtool ssh output {config.profile} --run-id {run_id} --follow" if active
            else f"devtool ssh retrieve {config.profile} --run-id {run_id}" if return_state == "recoverable"
            else ""
        )
        runs.append({
            "run_id": run_id,
            "state": state,
            "status": status,
            "active": active,
            "phase_count": len(phases),
            "started_unix": min((_safe_int(item.get("started_unix")) for item in phases if _safe_int(item.get("started_unix"))), default=0),
            "finished_unix": max((_safe_int(item.get("finished_unix")) for item in phases), default=0),
            "profile": config.profile,
            "destination": config.destination,
            "repository_fingerprint": str(first.get("repository_fingerprint") or ""),
            "repository_root": str(first.get("repository_root") or ""),
            "target": str(first.get("target") or first.get("federation_child_target") or ""),
            "workspace_id": str(first.get("workspace_id") or ""),
            "workspace_path": str(first.get("workspace_path") or ""),
            "source_sync_id": str(first.get("source_sync_id") or ""),
            "frozen_source_lock_digest": str(first.get("frozen_source_lock_digest") or ""),
            "typed_contract": dict(first.get("typed_contract") or {}),
            "lineage": dict(first.get("lineage") or {}),
            "output_state": "still-writing" if active else (
                "interrupted" if any(str(item.get("output_state")) == "interrupted" for item in phases)
                else "complete" if any(str(item.get("output_state")) == "complete" for item in phases)
                else "unavailable"
            ),
            "return_state": return_state,
            "return_recoverable": return_state == "recoverable",
            "return_bundles": return_states,
            "runstore": runstore,
            "test_summary": dict(runstore.get("summary") or {}),
            "artifacts": list(runstore.get("artifacts") or []),
            "build_artifacts": list(runstore.get("build_artifacts") or []),
            "logs": list(runstore.get("logs") or []),
            "recovery_hint": recovery_hint,
            "phases": phases,
            "latest_session": str(last.get("session") or ""),
        })
    runs.sort(key=lambda item: (_safe_int(item.get("started_unix")), str(item.get("run_id") or "")), reverse=True)
    return {
        "schema_version": 2,
        "kind": "ssh-runs-grouped",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "runs": runs,
        "records": source.get("records", []),
        "run_count": len(runs),
        "active_count": sum(1 for item in runs if item["active"]),
        "stale_count": int(source.get("stale_count") or 0),
    }
