from __future__ import annotations

import json
from typing import Any, Iterable


def hook_event_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    event = metadata.get("hook_event")
    return dict(event) if isinstance(event, dict) else None


def hook_events_from_metadata(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(metadata, dict):
        return []
    events: list[dict[str, Any]] = []
    event = hook_event_from_metadata(metadata)
    if event is not None:
        events.append(event)
    many = metadata.get("hook_events")
    if isinstance(many, list):
        events.extend(dict(item) for item in many if isinstance(item, dict))
    return events


def collect_hook_events(results: Iterable[Any], *, dedupe: bool = True) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for result in results:
        events.extend(hook_events_from_metadata(getattr(result, "metadata", None)))
    if not dedupe:
        return events
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for event in events:
        key = json.dumps(event, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique
