"""PRES-X01 presentation projections over canonical execution records.

This module deliberately renders *projections*.  It does not own process truth,
validation truth, progress truth, or reuse truth.  Every rendered field either
comes from an explicit RUN/EXO/RER/validation/environment reference or is a
pure, deterministic summary of those references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

_SECRET_RE = re.compile(r"(?i)(token|secret|password|passwd|credential|api[_-]?key|authorization)")


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def projection_digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def redact_value(key: str, value: Any) -> Any:
    if _SECRET_RE.search(key):
        return "<redacted>"
    if isinstance(value, str) and re.search(r"(?i)(bearer\s+[a-z0-9._-]+|token=[^\s]+|password=[^\s]+)", value):
        return "<redacted>"
    return value


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): redact_value(str(k), v) for k, v in sorted(values.items())}


class PresentationSurface(str, Enum):
    MACHINE = "machine"
    TERMINAL = "terminal"
    TUI = "tui"
    DESKTOP = "desktop"


@dataclass(frozen=True)
class CanonicalReference:
    authority: str
    ref: str
    kind: str
    digest: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = {"authority": self.authority, "ref": self.ref, "kind": self.kind}
        if self.digest:
            data["digest"] = self.digest
        return data


@dataclass(frozen=True)
class PresentationEvent:
    id: str
    authority: str
    status: str
    label: str
    timestamp: str | None = None
    progress: float | None = None
    refs: tuple[CanonicalReference, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "authority": self.authority,
            "status": self.status,
            "label": self.label,
            "refs": [r.as_dict() for r in self.refs],
        }
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp
        if self.progress is not None:
            payload["progress"] = self.progress
        if self.detail:
            payload["detail"] = redact_mapping(self.detail)
        return payload


@dataclass(frozen=True)
class PresentationProjection:
    surface: PresentationSurface
    title: str
    summary: str
    status: str
    events: tuple[PresentationEvent, ...]
    refs: tuple[CanonicalReference, ...]
    truncated: bool = False
    cursor: str | None = None
    generated_truth: bool = False

    def as_machine(self) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "surface": self.surface.value,
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "events": [event.as_dict() for event in self.events],
            "refs": [ref.as_dict() for ref in self.refs],
            "truncated": self.truncated,
            "cursor": self.cursor,
            "generated_truth": self.generated_truth,
        }
        payload["projection_digest"] = projection_digest({k: v for k, v in payload.items() if k != "projection_digest"})
        return payload

    def terminal_rows(self) -> list[str]:
        rows = [f"{self.title}: {self.status} — {self.summary}"]
        for event in self.events:
            pct = "" if event.progress is None else f" {event.progress:.0%}"
            rows.append(f"{event.status.upper():9} {event.label}{pct} [{event.authority}:{event.id}]")
        if self.truncated:
            rows.append(f"… truncated; cursor={self.cursor}")
        return rows


def status_from_exo(records: Sequence[Mapping[str, Any]], *, surface: PresentationSurface = PresentationSurface.MACHINE, limit: int = 50) -> PresentationProjection:
    limited = list(records[:limit])
    truncated = len(records) > limit
    events: list[PresentationEvent] = []
    refs: list[CanonicalReference] = []
    for index, record in enumerate(limited):
        authority = str(record.get("authority", "EXO"))
        ref = str(record.get("ref", f"exo:{index}"))
        digest = str(record.get("digest", "")) or None
        cref = CanonicalReference(authority=authority, ref=ref, kind=str(record.get("kind", "execution-node")), digest=digest)
        refs.append(cref)
        events.append(
            PresentationEvent(
                id=str(record.get("id", ref)),
                authority=authority,
                status=str(record.get("status", "unknown")),
                label=str(record.get("label", record.get("id", ref))),
                timestamp=record.get("timestamp"),
                progress=record.get("progress"),
                refs=(cref,),
                detail=dict(record.get("detail", {})),
            )
        )
    failed = any(event.status in {"failed", "error"} for event in events)
    running = any(event.status in {"running", "pending"} for event in events)
    status = "failed" if failed else "running" if running else "passed" if events else "empty"
    summary = f"{len(events)} canonical event(s) projected from EXO/RUN records"
    cursor = None if not truncated else refs[-1].ref if refs else "start"
    return PresentationProjection(surface, "Execution status", summary, status, tuple(events), tuple(refs), truncated, cursor)


def validation_verdict_projection(contract: Mapping[str, Any], evidence: Mapping[str, Any], *, surface: PresentationSurface = PresentationSurface.MACHINE) -> PresentationProjection:
    required = tuple(contract.get("required", ()))
    optional = tuple(contract.get("optional", ()))
    satisfied = set(evidence.get("satisfied", ()))
    not_applicable = set(evidence.get("not_applicable", ()))
    skipped = set(evidence.get("skipped", ()))
    missing = [check for check in required if check not in satisfied and check not in not_applicable]
    invalid_skips = [check for check in required if check in skipped and check not in not_applicable]
    status = "passed" if not missing and not invalid_skips else "failed"
    refs = tuple(CanonicalReference("validation", str(r), "validation-evidence") for r in evidence.get("refs", ()))
    events = [
        PresentationEvent(
            id=str(check),
            authority="validation",
            status="not-applicable" if check in not_applicable else "passed" if check in satisfied else "failed",
            label=str(check),
            refs=refs,
            detail={"required": True},
        )
        for check in required
    ]
    for check in optional:
        events.append(
            PresentationEvent(
                id=str(check),
                authority="validation",
                status="passed" if check in satisfied else "optional",
                label=str(check),
                refs=refs,
                detail={"required": False},
            )
        )
    summary = "validation contract satisfied" if status == "passed" else f"missing={missing} invalid_skips={invalid_skips}"
    return PresentationProjection(surface, "Validation verdict", summary, status, tuple(events), refs)


def reuse_explain_projection(decisions: Sequence[Mapping[str, Any]], *, surface: PresentationSurface = PresentationSurface.MACHINE) -> PresentationProjection:
    events: list[PresentationEvent] = []
    refs: list[CanonicalReference] = []
    for i, decision in enumerate(decisions):
        disposition = str(decision.get("disposition", "run"))
        authority = str(decision.get("authority", "RER"))
        ref = str(decision.get("ref", f"rer:{i}"))
        cref = CanonicalReference(authority, ref, "reuse-decision", decision.get("digest"))
        refs.append(cref)
        status = "reused" if disposition == "reuse" else "rerun" if disposition in {"run", "rerun", "invalidated"} else disposition
        events.append(
            PresentationEvent(
                id=str(decision.get("node", ref)),
                authority=authority,
                status=status,
                label=str(decision.get("reason", disposition)),
                refs=(cref,),
                detail={"source_proof": decision.get("source_proof"), "override": decision.get("override")},
            )
        )
    status = "rerun" if any(event.status == "rerun" for event in events) else "reused" if events else "empty"
    return PresentationProjection(surface, "Reuse explanation", f"{len(events)} reuse decision(s)", status, tuple(events), tuple(refs))


def compose_dashboard(*projections: PresentationProjection, surface: PresentationSurface = PresentationSurface.TUI, limit: int = 100) -> PresentationProjection:
    events: list[PresentationEvent] = []
    refs: list[CanonicalReference] = []
    for projection in projections:
        events.extend(projection.events)
        refs.extend(projection.refs)
    truncated = len(events) > limit
    if truncated:
        events = events[:limit]
    status_order = {"failed": 3, "rerun": 2, "running": 2, "passed": 1, "reused": 1, "empty": 0}
    status = max((p.status for p in projections), key=lambda s: status_order.get(s, 0), default="empty")
    return PresentationProjection(
        surface=surface,
        title="Devtool dashboard",
        summary=f"{len(projections)} projection(s), {len(events)} rendered event(s)",
        status=status,
        events=tuple(events),
        refs=tuple(refs),
        truncated=truncated,
        cursor=events[-1].id if truncated and events else None,
    )


def environment_doctor_projection(records: Sequence[Mapping[str, Any]], *, surface: PresentationSurface = PresentationSurface.TERMINAL) -> PresentationProjection:
    events: list[PresentationEvent] = []
    refs: list[CanonicalReference] = []
    for i, record in enumerate(records):
        cref = CanonicalReference("ENV", str(record.get("ref", f"env:{i}")), "environment-explain", record.get("digest"))
        refs.append(cref)
        events.append(
            PresentationEvent(
                id=str(record.get("id", cref.ref)),
                authority="ENV",
                status=str(record.get("status", "info")),
                label=str(record.get("message", record.get("id", cref.ref))),
                refs=(cref,),
                detail=redact_mapping(dict(record.get("detail", {}))),
            )
        )
    status = "failed" if any(e.status in {"error", "failed"} for e in events) else "passed" if events else "empty"
    return PresentationProjection(surface, "Environment doctor", f"{len(events)} ENV explanation(s)", status, tuple(events), tuple(refs))


def assert_projection_is_reference_only(projection: PresentationProjection) -> None:
    machine = projection.as_machine()
    forbidden = ("stdout", "stderr", "argv", "heartbeat_owner", "process_attempt")
    encoded = _stable_json(machine)
    leaked = [item for item in forbidden if item in encoded]
    if leaked:
        raise AssertionError(f"projection contains validation/execution-owned truth: {leaked}")
    if machine.get("generated_truth") is not False:
        raise AssertionError("presentation projection attempted to own generated truth")
