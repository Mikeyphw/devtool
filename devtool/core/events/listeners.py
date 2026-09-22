from __future__ import annotations

from collections import deque
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any

from .events import DevtoolEvent
from ..logging import get_logger


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            safe[str(key)] = _json_safe(item)
        return safe
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def event_to_dict(event: DevtoolEvent) -> dict[str, Any]:
    payload = asdict(event) if is_dataclass(event) else {"value": str(event)}
    payload = _json_safe(payload)
    return {
        "type": type(event).__name__,
        "event_type": str(getattr(event, "protocol_type", "execution.event")),
        **payload,
    }


class JsonEventListener:
    """Collect events for JSON/API consumers."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: DevtoolEvent) -> None:
        self.events.append(event_to_dict(event))


class TuiEventListener:
    """Bounded in-memory event state suitable for terminal UI snapshots."""

    def __init__(self, *, max_events: int = 100) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=max_events)

    def __call__(self, event: DevtoolEvent) -> None:
        self.events.append(event_to_dict(event))

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.events)


class LoggingEventListener:
    def __call__(self, event: DevtoolEvent) -> None:
        get_logger("EVENT").info("%s", json.dumps(event_to_dict(event), ensure_ascii=False, sort_keys=True))


class ConsoleEventListener:
    """Human event trace used only in verbose/trace mode."""

    def __init__(self, console: Any) -> None:
        self.console = console

    def __call__(self, event: DevtoolEvent) -> None:
        payload = event_to_dict(event)
        kind = payload.pop("type")
        detail = " ".join(f"{key}={value}" for key, value in payload.items() if value not in ({}, "", None))
        self.console.print(f"[dim][{kind}] {detail}[/dim]".rstrip())


__all__ = ["event_to_dict", "JsonEventListener", "TuiEventListener", "LoggingEventListener", "ConsoleEventListener"]
