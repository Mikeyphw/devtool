"""Single inline Rich host. Presentation only: native execution owns its I/O.

Never redirect stdout/stderr, start an alternate screen, or permit nested
Live owners. Geometry is observed from the attached terminal, not guessed
from stale COLUMNS/LINES. No clipboard, network or execution authority.
"""
from __future__ import annotations

import os
import threading
from typing import Any

from rich.console import Console
from rich.live import Live

_OWNER = threading.Lock()


def terminal_geometry(console: Console, fd: int | None = None) -> tuple[int, int]:
    try:
        size = os.get_terminal_size(console.file.fileno() if fd is None else fd)
        if size.columns > 0 and size.lines > 0:
            return size.columns, size.lines
    except (OSError, AttributeError, ValueError, TypeError):
        pass
    return max(1, int(console.width)), max(1, int(console.height))


class InlineLive:
    """Exclusive main-thread owner with idempotent, exception-safe release."""
    def __init__(self, frame: Any, *, console: Console):
        self.console = console
        self.frame = frame
        self._live: Live | None = None
        self._owns = False
        self._thread: int | None = None

    def start(self) -> None:
        if self._owns:
            if self._thread != threading.get_ident():
                raise RuntimeError("Inline Live belongs to another thread")
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('Inline Live requires foreground main thread')
        if not _OWNER.acquire(blocking=False):
            raise RuntimeError('Another inline Live owner is active')
        self._owns = True
        self._thread = threading.get_ident()
        try:
            self._live = Live(self.frame, console=self.console, screen=False,
                              transient=False, auto_refresh=False,
                              redirect_stdout=False, redirect_stderr=False,
                              vertical_overflow='ellipsis')
            self._live.start(refresh=True)
        except BaseException:
            try:self.stop()
            except Exception:pass
            raise

    def update(self, frame: Any, *, refresh: bool = True) -> None:
        if not self._owns or self._live is None or self._thread != threading.get_ident():
            raise RuntimeError('Inline Live update outside foreground owner')
        self.frame = frame
        self._live.update(frame, refresh=refresh)

    def resize(self, fd: int | None = None) -> bool:
        width, height = terminal_geometry(self.console, fd)
        changed = (width, height) != (self.console.width, self.console.height)
        self.console.width, self.console.height = width, height
        return changed

    def stop(self) -> None:
        if not self._owns:
            return
        if self._thread != threading.get_ident():
            raise RuntimeError('Inline Live release outside foreground owner')
        live, self._live = self._live, None
        try:
            if live is not None:
                live.stop()
        finally:
            self._owns = False
            self._thread = None
            _OWNER.release()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.stop()
        except Exception:
            if exc is None:
                raise
        return False
