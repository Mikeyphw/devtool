from __future__ import annotations

from collections import defaultdict
from typing import Callable, DefaultDict, Type

from .events import DevtoolEvent

Listener = Callable[[DevtoolEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: DefaultDict[type[DevtoolEvent], list[Listener]] = defaultdict(list)

    def subscribe(self, event_type: Type[DevtoolEvent], listener: Listener) -> None:
        self._listeners[event_type].append(listener)

    def publish(self, event: DevtoolEvent) -> None:
        for listener in self._listeners[type(event)]:
            listener(event)
