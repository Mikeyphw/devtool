from __future__ import annotations

from collections.abc import Iterable
from typing import Any

MAX_EVIDENCE_REFS = 64
MAX_EVIDENCE_REF_CHARS = 2048
MAX_LOG_REF_CHARS = 4096
MAX_PROCESS_LAST_OUTPUT_CHARS = 4096
MAX_JOURNAL_PROCESS_OUTPUT_CHARS = 2048


def bounded_text(value: Any, *, limit: int) -> str:
    text = str(value or "")
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit]


def bounded_log_ref(value: Any) -> str:
    return bounded_text(value, limit=MAX_LOG_REF_CHARS)


def bounded_evidence_ref(value: Any) -> str:
    return bounded_text(value, limit=MAX_EVIDENCE_REF_CHARS)


def merge_bounded_evidence(existing: Iterable[Any], incoming: Iterable[Any]) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for item in (*tuple(existing or ()), *tuple(incoming or ())):
        value = bounded_evidence_ref(item)
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    if len(values) <= MAX_EVIDENCE_REFS:
        return tuple(values)
    # Keep the oldest causal references and the newest diagnostic/log references.
    head = max(1, MAX_EVIDENCE_REFS // 4)
    return tuple(values[:head] + values[-(MAX_EVIDENCE_REFS - head):])


def bound_event_reference_fields(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("evidence"), (list, tuple)):
        payload["evidence"] = list(merge_bounded_evidence((), payload["evidence"]))
    if "log_path" in payload:
        payload["log_path"] = bounded_log_ref(payload.get("log_path"))
    return payload


__all__ = [
    "MAX_EVIDENCE_REFS",
    "MAX_EVIDENCE_REF_CHARS",
    "MAX_LOG_REF_CHARS",
    "MAX_PROCESS_LAST_OUTPUT_CHARS",
    "MAX_JOURNAL_PROCESS_OUTPUT_CHARS",
    "bounded_text",
    "bounded_log_ref",
    "bounded_evidence_ref",
    "merge_bounded_evidence",
    "bound_event_reference_fields",
]
