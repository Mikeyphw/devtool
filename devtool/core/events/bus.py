from __future__ import annotations

from collections import defaultdict
from typing import Callable, DefaultDict, Type

from .events import DevtoolEvent

Listener = Callable[[DevtoolEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: DefaultDict[type[DevtoolEvent], list[Listener]] = defaultdict(list)
        self._all_listeners: list[Listener] = []

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

    def publish(self, event: DevtoolEvent) -> None:
        for listener in tuple(self._listeners[type(event)]):
            listener(event)
        for listener in tuple(self._all_listeners):
            listener(event)
