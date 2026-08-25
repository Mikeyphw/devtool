from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from .bus import EventBus
from .events import DevtoolEvent
from .listeners import ConsoleEventListener, JsonEventListener, LoggingEventListener, TuiEventListener

_BUS = EventBus()
_TUI = TuiEventListener()
_LOGGER = LoggingEventListener()
_BUS.subscribe_all(_TUI)
_BUS.subscribe_all(_LOGGER)
_CONSOLE_LISTENER: ConsoleEventListener | None = None


def get_event_bus() -> EventBus:
    return _BUS


def publish_event(event: DevtoolEvent) -> None:
    _BUS.publish(event)


def configure_event_console(console, *, enabled: bool) -> None:
    global _CONSOLE_LISTENER
    if _CONSOLE_LISTENER is not None:
        _BUS.unsubscribe_all(_CONSOLE_LISTENER)
        _CONSOLE_LISTENER = None
    if enabled:
        _CONSOLE_LISTENER = ConsoleEventListener(console)
        _BUS.subscribe_all(_CONSOLE_LISTENER)


def tui_event_snapshot() -> list[dict]:
    return _TUI.snapshot()


@contextmanager
def collect_json_events() -> Iterator[JsonEventListener]:
    listener = JsonEventListener()
    _BUS.subscribe_all(listener)
    try:
        yield listener
    finally:
        _BUS.unsubscribe_all(listener)


__all__ = ["get_event_bus", "publish_event", "configure_event_console", "tui_event_snapshot", "collect_json_events"]
