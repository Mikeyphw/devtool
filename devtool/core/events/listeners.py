from __future__ import annotations

from collections import deque
from dataclasses import asdict, is_dataclass
import json
from typing import Any

from .events import DevtoolEvent
from ..logging import get_logger


def event_to_dict(event: DevtoolEvent) -> dict[str, Any]:
    payload = asdict(event) if is_dataclass(event) else {"value": str(event)}
    return {"type": type(event).__name__, **payload}


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
