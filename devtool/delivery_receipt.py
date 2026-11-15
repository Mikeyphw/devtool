"""S6: one bounded presentation stream over the existing durable delivery journal.

This module is observational: it neither writes journals nor contacts GitHub.
The existing registrar alone decides whether required CI has qualified.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import re

_MAX_EVENTS = 160
_MAX_ROWS = 25
_MAX_FAILURES = 25
_ID = re.compile(r"[0-9a-f]{40}\Z")
_SAFE = re.compile(r"[\x00-\x1f\x7f]|(?i:token|password|secret|credential|authorization|signature|sig=|api.key|bearer)")
_LIFECYCLE = {
    "session_started": "source",
    "mutation_intent": "remote-intent",
    "mutation_confirmed": "remote-reconciliation",
    "pr_observed": "pull-request",
    "ci_observed": "ci",
}


def _label(value: Any, limit: int = 140) -> str:
    text = str(value or "")
    if len(text) > limit or _SAFE.search(text) or "http" in text.lower():
        return "[provider label omitted]"
    return text


def _positive(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _run_url(repository: str, run: int) -> str:
    return f"https://github.com/{repository}/actions/runs/{run}"


def build_stream(record: Mapping[str, Any]) -> dict[str, Any]:
    """Reconcile stored lifecycle events and the latest verified observations.

    Historical snapshots are explicitly labelled; they are never refreshed or
    promoted to a new source SHA, and no remote mutation is replayed.
    """
    repository = str(record.get("repository") or "")
    remote_bound = bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository))
    if not remote_bound and (record.get("pr") or (record.get("ci") or {}).get("runs")):
        raise ValueError("Remote evidence requires a canonical repository")
    source = str(record.get("head_sha") or "")
    if not _ID.fullmatch(source):
        raise ValueError("Delivery evidence requires a full source SHA")
    original = record.get("events") or []
    if not isinstance(original, list):
        raise ValueError("Invalid delivery event journal")
    rows = []
    # Preserve all mutation intent records, even if periodic CI observations
    # were compacted by the durable S1 journal.
    important = [e for e in original if isinstance(e, dict) and e.get("kind") != "ci_observed"]
    ci_history = [e for e in original if isinstance(e, dict) and e.get("kind") == "ci_observed"][-48:]
    for e in sorted(important + ci_history, key=lambda x: str(x.get("at") or ""))[-100:]:
        kind = e.get("kind")
        if kind not in _LIFECYCLE:
            continue
        row = {"kind": kind, "stage": _LIFECYCLE[kind], "at": str(e.get("at") or "")[:40],
               "source_sha": source, "historical": True}
        if kind in ("mutation_intent", "mutation_confirmed"):
            row["operation"] = _label(e.get("operation"), 32)
            row["state"] = "unresolved" if kind == "mutation_intent" else "confirmed"
        elif kind == "ci_observed":
            row["state"] = _label(e.get("state"), 50)
        elif kind == "pr_observed":
            row["number"] = _positive(e.get("number"))
        else:
            row["state"] = _label(e.get("stage"), 40)
        rows.append(row)
    pr = record.get("pr")
    if isinstance(pr, Mapping) and _positive(pr.get("number")):
        number = pr["number"]
        if pr.get("head_sha") == source and pr.get("url") == f"https://github.com/{repository}/pull/{number}":
            rows.append({"kind": "pr_snapshot", "stage": "pull-request", "number": number,
                         "url": pr["url"], "source_sha": source, "historical": True})
    ci = record.get("ci") or {}
    if not isinstance(ci, Mapping):
        raise ValueError("Invalid CI evidence")
    if ci and ci.get("source_sha") != source:
        raise ValueError("CI evidence differs from delivery source")
    runs = ci.get("runs") or []
    if not isinstance(runs, list) or len(runs) > _MAX_ROWS:
        raise ValueError("CI evidence run inventory is unbounded")
    seen = set()
    for r in runs:
        if not isinstance(r, Mapping):
            raise ValueError("Invalid CI run row")
        rid, attempt = _positive(r.get("id")), _positive(r.get("attempt"))
        if not rid or not attempt or (rid, attempt) in seen:
            raise ValueError("Missing or duplicate exact run identity")
        seen.add((rid, attempt))
        if r.get("url") != _run_url(repository, rid):
            raise ValueError("Noncanonical provider run URL")
        rows.append({"kind": "workflow_snapshot", "stage": "ci", "historical": True,
                     "run_id": rid, "attempt": attempt, "workflow_id": _positive(r.get("workflow_id")),
                     "name": _label(r.get("name")), "event": _label(r.get("event"), 35),
                     "status": _label(r.get("status"), 32),
                     "conclusion": _label(r.get("conclusion"), 32),
                     "url": _run_url(repository, rid), "source_sha": source})
    diagnosis = ci.get("diagnosis") or {}
    if not isinstance(diagnosis, Mapping):
        raise ValueError("Invalid diagnostic snapshot")
    failures = diagnosis.get("failures") or []
    if not isinstance(failures, list) or len(failures) > _MAX_FAILURES:
        raise ValueError("Invalid failed-job inventory")
    for failure in failures:
        if not isinstance(failure, Mapping):
            raise ValueError("Invalid failed-run evidence")
        rid, attempt = _positive(failure.get("run_id")), _positive(failure.get("attempt"))
        if not rid or not attempt or (rid, attempt) not in seen:
            raise ValueError("Failed job is not bound to an observed run attempt")
        for job in (failure.get("jobs") or [])[:8]:
            if not isinstance(job, Mapping) or not _positive(job.get("job_id")):
                continue
            jid = job["job_id"]
            url = f"{_run_url(repository, rid)}/attempts/{attempt}/job/{jid}"
            rows.append({"kind": "failed_job_snapshot", "stage": "diagnostics", "historical": True,
                         "run_id": rid, "attempt": attempt, "job_id": jid,
                         "name": _label(job.get("name")),
                         "classification": _label(job.get("classification"), 60),
                         "conclusion": _label(job.get("conclusion"), 35),
                         "url": url, "source_sha": source})
    if len(rows) > _MAX_EVENTS:
        # Bounded view only, never mutate or truncate the durable source journal.
        rows = rows[-_MAX_EVENTS:]
    return {"schema": "devtool.delivery-event-stream.v1", "source_sha": source,
            "events": rows, "total_source_events": len(original),
            "latest_snapshot_at": str(record.get("updated_at") or "")[:40],
            "read_only": True, "remote_mutations": False}


def build_receipt(status: Mapping[str, Any]) -> dict[str, Any]:
    """Build a durable machine-readable receipt from a single status snapshot."""
    stream = status.get("event_stream")
    if not isinstance(stream, Mapping) or stream.get("source_sha") != status.get("head_sha"):
        raise ValueError("Delivery event stream is not source-bound")
    ci = status.get("ci") or {}
    diagnosis = ci.get("diagnosis") or {}
    verified_at_last_observation = (ci.get("state") == "REQUIRED_CHECKS_PASSED"
                                    and ci.get("checks_qualified") is True)
    source_matching = status.get("source_binding") == "matching"
    unresolved = [i.get("operation") for i in status.get("intents", [])
                  if i.get("state") != "confirmed"]
    resumable = status.get("resumable") is True and source_matching and not unresolved
    if unresolved or not source_matching:
        next_action = "Reconcile source drift and uncertain intents; do not retry mutations"
    elif verified_at_last_observation:
        next_action = "Review independent release gates; no automatic merge or publish"
    elif ci.get("state") == "CHECKS_FAILED":
        next_action = "Inspect exact failed-job URLs and fix source; no automatic rerun"
    else:
        next_action = "Resume read-only CI observation for this exact source and PR"
    return {"schema": "devtool.delivery-evidence.v1",
            "session_id": status.get("id"), "repository": status.get("repository"),
            "source_sha": status.get("head_sha"), "branch": status.get("head"),
            "base": status.get("base"), "source_binding": status.get("source_binding", "unavailable"),
            "pr": status.get("pr"), "ci_state": ci.get("state", "not-observed"),
            "ci_classification": diagnosis.get("classification", "not-observed"),
            "required_workflows": list(ci.get("required") or [])[:_MAX_ROWS],
            "required_ci_passed_at_last_observation": verified_at_last_observation,
            "current_remote_ci_reverified": False, "release_qualified": False,
            "resumable_without_replay": resumable, "unresolved_intents": unresolved,
            "next_safe_action": next_action, "event_stream": stream}


def render_human(receipt: Mapping[str, Any]) -> str:
    """Stable human-readable view consumed by plain CLI and Rich launchers."""
    lines = ["Devtool S6 delivery evidence (read-only)",
             f"Session: {receipt.get('session_id') or '-'}",
             f"Source: {receipt.get('repository')} @ {receipt.get('source_sha')}",
             f"Binding: {receipt.get('source_binding')}",
             f"CI snapshot: {receipt.get('ci_state')} ({receipt.get('ci_classification')})",
             "CI observation is historical; no live checks were performed by this report."]
    for event in receipt.get("event_stream", {}).get("events", []):
        if event["kind"] == "pr_snapshot":
            lines.append(f"PR #{event['number']}: {event['url']}")
        elif event["kind"] == "workflow_snapshot":
            lines.append(f"Workflow: {event['name']} run={event['run_id']} attempt={event['attempt']} "
                         f"status={event['status']} conclusion={event['conclusion']} {event['url']}")
        elif event["kind"] == "failed_job_snapshot":
            lines.append(f"Failed job: {event['name']} ({event['classification']}) "
                         f"run={event['run_id']} attempt={event['attempt']} {event['url']}")
    lines.append("Recovery: " + str(receipt.get("next_safe_action")))
    lines.append("Release qualification: NOT PERFORMED")
    return "\n".join(lines)


def render_rich(receipt: Mapping[str, Any], console: Any = None) -> None:
    """Rich presentation of the same receipt; Textual can reuse its event_stream."""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel

    view = console if console is not None else Console()
    view.print(Panel(
        f"[bold]{receipt.get('repository')}[/bold]\n"
        f"Source: {receipt.get('source_sha')}\n"
        f"Binding: {receipt.get('source_binding')}\n"
        f"CI: {receipt.get('ci_state')} (saved observation; not live)",
        title="Devtool S6 delivery evidence", expand=False))
    table = Table(title="Source-bound delivery events")
    for column in ("Stage", "Event", "Run / attempt", "State"):
        table.add_column(column)
    for e in receipt.get('event_stream', {}).get('events', []):
        run = (f"{e['run_id']} / {e['attempt']}" if 'run_id' in e else "-")
        state = e.get('conclusion') or e.get('status') or e.get('state') or "observed"
        table.add_row(e.get('stage', '-'), e.get('name') or e.get('kind', '-'),
                      run, str(state))
    view.print(table)
    view.print("Recovery: " + str(receipt.get('next_safe_action')))
    view.print("Release qualification: NOT PERFORMED")
