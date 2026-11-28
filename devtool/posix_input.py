"""Shared bounded POSIX input ownership for native inline read-only views."""
from __future__ import annotations

import os
import select
import signal
import sys
import threading
import time
from typing import Callable

_INPUT_OWNER = threading.Lock()


def read_key(fd: int, timeout: float = .15) -> str | None:
    """None is timeout, empty string EOF; arrows tolerate fragmented reads.

    Consume at most a bounded CSI sequence. Unknown escapes never dispatch
    application keys.
    """
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None
    raw = os.read(fd, 1)
    if not raw:
        return ''
    if raw != b'\x1b':
        return raw.decode('ascii', 'ignore') or '\x00'
    deadline = time.monotonic() + .08
    seq = b''
    while len(seq) < 12:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            break
        piece = os.read(fd, 1)
        if not piece:
            break
        seq += piece
        if len(seq) == 1 and piece not in (b'[', b'O'):
            return '\x1b'
        if len(seq) >= 2 and 0x40 <= piece[0] <= 0x7e:
            break
    return {'[A':'up', '[B':'down', 'OA':'up', 'OB':'down'}.get(seq.decode('ascii','ignore'), '\x1b')


class PosixInput:
    """Exclusive cbreak lease with prior signal/termios restoration."""
    def __init__(self, fd: int):
        self.fd = fd
        self.saved = None
        self.handlers = {}
        self.owned = False

    def start(self):
        if self.owned:
            return self
        if os.name != 'posix' or threading.current_thread() is not threading.main_thread():
            raise RuntimeError('POSIX input requires foreground main thread')
        if not _INPUT_OWNER.acquire(blocking=False):
            raise RuntimeError('Another POSIX input owner is active')
        self.owned = True
        try:
            import termios, tty
            self.saved = termios.tcgetattr(self.fd)
            def terminate(signum, frame):
                raise SystemExit(128 + signum)
            self.handlers[signal.SIGINT] = signal.signal(signal.SIGINT, signal.default_int_handler)
            for signum in (signal.SIGTERM, signal.SIGHUP):
                self.handlers[signum] = signal.signal(signum, terminate)
            tty.setcbreak(self.fd)
            # Preserve interrupt semantics even if a parent PTY relay left ISIG
            # disabled. Restore the original mode exactly on lease release.
            mode = termios.tcgetattr(self.fd)
            mode[3] |= termios.ISIG
            mode[6][termios.VINTR] = b"\x03"
            termios.tcsetattr(self.fd, termios.TCSANOW, mode)
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        if not self.owned:
            return
        try:
            if self.saved is not None:
                import termios
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        finally:
            self.saved = None
            try:
                for signum, previous in self.handlers.items():
                    signal.signal(signum, previous)
            finally:
                self.handlers.clear()
                self.owned = False
                _INPUT_OWNER.release()

    def read(self, timeout: float = .15):
        if not self.owned:
            raise RuntimeError('Input lease not active')
        return read_key(self.fd, timeout)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:
            if exc is None:
                raise
        return False


class KeyboardWorker:
    """Optional key reader; callback owns state only, never terminal rendering."""
    def __init__(self, monitor):
        self.monitor = monitor
        self.stop_requested = False
        self.thread = None
        self.lease = None
        self.fd = None

    def start(self):
        if os.name != 'posix':
            return
        try:
            if not sys.stdin.isatty():
                return
            self.fd = sys.stdin.fileno()
            self.lease = PosixInput(self.fd).start()
            self.thread = threading.Thread(target=self._run, daemon=True, name='devtool-rich-monitor-keys')
            self.thread.start()
        except (OSError, ValueError, AttributeError, RuntimeError):
            self.stop()

    def _run(self):
        while not self.stop_requested:
            try:
                value = read_key(self.fd)
                if value == '':
                    return
                if value is None:
                    continue
                try:
                    self.monitor.key(value)
                except Exception as exc:
                    print('M6_KEYBOARD_CALLBACK_WARNING '+type(exc).__name__+'; keyboard remains active', file=sys.stderr, flush=True)
            except (OSError, ValueError):
                return

    def stop(self):
        self.stop_requested = True
        try:
            if self.thread is not None and self.thread.is_alive():
                self.thread.join(timeout=.5)
        finally:
            if self.lease is not None:
                lease, self.lease = self.lease, None
                lease.close()
