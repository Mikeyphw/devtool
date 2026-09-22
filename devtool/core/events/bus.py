from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Callable, DefaultDict, Type

from ..execution_contract import RunId
from ..logging import get_logger
from .events import DevtoolEvent

Listener = Callable[[DevtoolEvent], None]


class EventBus:
    """Synchronous, fault-contained execution event bus.

    Publication assigns the canonical event envelope and dispatches to both the
    concrete compatibility type and any subscribed canonical base types.  This
    lets old ``TaskStarted`` publishers feed new ``NodeStarted`` consumers.
    """

    def __init__(self) -> None:
        self._listeners: DefaultDict[type[DevtoolEvent], list[Listener]] = defaultdict(list)
        self._all_listeners: list[Listener] = []
        self._sequence_by_run: dict[str, int] = {}
        self._sequence_lock = Lock()
        self._fallback_run_id = str(RunId.current(create=False) or RunId.new(prefix="event-run"))

    def subscribe(self, event_type: Type[DevtoolEvent], listener: Listener) -> None:
        self._listeners[event_type].append(listener)

    def unsubscribe(self, event_type: Type[DevtoolEvent], listener: Listener) -> None:
        listeners = self._listeners.get(event_type, [])
        if listener in listeners:
            listeners.remove(listener)

    def subscribe_all(self, listener: Listener) -> None:
        if listener not in self._all_listeners:
            self._all_listeners.append(listener)

    def unsubscribe_all(self, listener: Listener) -> None:
        if listener in self._all_listeners:
            self._all_listeners.remove(listener)

    def _safe_notify(self, listener: Listener, event: DevtoolEvent) -> None:
        try:
            listener(event)
        except Exception as exc:  # pragma: no cover - defensive logging path
            get_logger("EVENT").warning(
                "Devtool event listener %r failed for %s: %s",
                listener,
                type(event).__name__,
                exc,
            )

    def _prepare(self, event: DevtoolEvent) -> None:
        event.promote_legacy_identity()
        if not event.run_id:
            event.run_id = str(RunId.current(create=False) or self._fallback_run_id)
        with self._sequence_lock:
            current = self._sequence_by_run.get(event.run_id, 0)
            if int(event.sequence) >= 0:
                sequence = int(event.sequence)
                if current and sequence <= current:
                    raise ValueError(
                        f"event sequence regression for run {event.run_id!r}: {sequence} <= {current}"
                    )
                self._sequence_by_run[event.run_id] = sequence
            else:
                sequence = current + 1
                self._sequence_by_run[event.run_id] = sequence
        event.complete_envelope(sequence=sequence)

    def publish(self, event: DevtoolEvent) -> None:
        if not isinstance(event, DevtoolEvent):
            raise TypeError(f"EventBus only accepts DevtoolEvent values, got {type(event).__name__}")
        self._prepare(event)

        notified: list[Listener] = []
        # ``isinstance`` is deliberate: compatibility Task/Build events are
        # subclasses of the canonical Node/Run protocol events.
        for event_type, listeners in tuple(self._listeners.items()):
            if not isinstance(event, event_type):
                continue
            for listener in tuple(listeners):
                if listener in notified:
                    continue
                notified.append(listener)
                self._safe_notify(listener, event)
        for listener in tuple(self._all_listeners):
            if listener in notified:
                continue
            notified.append(listener)
            self._safe_notify(listener, event)
