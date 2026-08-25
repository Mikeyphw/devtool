"""Responsive terminal progress rendering for desktop and narrow Termux shells.

The live renderer keeps the active operation visually dominant while avoiding a
stack of completed panels.  A command has two distinct progress concepts:

* pipeline progress -- where the current phase sits in the overall Devtool run;
* phase progress -- the exact count/percentage reported by the active tool.

The core three-line renderer remains available for tests and non-validation
consumers.  ``ThreeLineProgressBody`` adds the optional pipeline header/bar when
phase metadata is present.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import os
import re
import select
import sys
import threading
import time
from typing import Callable

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.cells import cell_len, set_cell_size
from rich.measure import Measurement
from rich.panel import Panel
from rich.text import Text

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w:/])/(?:[^\s\[\]{}<>\"'`]+)")
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class TuiDisplayMode(str, Enum):
    """Interactive views available while a command is running."""

    COMPACT = "compact"
    VERBOSE = "verbose"
    LOGS = "logs"
    HELP = "help"
    CONFIRM_STOP = "confirm-stop"


class TuiInspectionController:
    """Own keyboard shortcuts and a bounded live-output buffer for Rich TUI runs.

    The controller is deliberately presentation-only. Command execution remains
    owned by the runner, and the optional stop callback is injected after the
    child process exists. This keeps non-interactive/CI behavior unchanged.
    """

    def __init__(
        self,
        console: Console,
        *,
        max_lines: int = 400,
        force_interactive: bool | None = None,
    ) -> None:
        self.console = console
        self.max_lines = max(20, int(max_lines))
        self.mode = TuiDisplayMode.COMPACT
        self._previous_mode = TuiDisplayMode.COMPACT
        self._lines: deque[str] = deque(maxlen=self.max_lines)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._reader_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_change: Callable[[], None] | None = None
        self._stop_callback: Callable[[], None] | None = None
        self._stop_requested = False
        self._scroll_offset = 0
        self._page_size = 10
        self._force_interactive = force_interactive
        self.enabled = self._detect_interactive() if force_interactive is None else bool(force_interactive)

    def _detect_interactive(self) -> bool:
        disabled = os.environ.get("DEVTOOL_TUI_SHORTCUTS", "").strip().lower() in {"0", "false", "no", "off"}
        if disabled:
            return False
        if os.environ.get("CI", "").strip().lower() in {"1", "true", "yes", "on"}:
            return False
        if not bool(self.console.is_terminal or self.console.is_interactive):
            return False
        stdin = getattr(sys, "stdin", None)
        if stdin is None:
            return False
        try:
            return bool(stdin.isatty())
        except (AttributeError, OSError, ValueError):
            return False

    @property
    def listening(self) -> bool:
        thread = self._thread
        return bool(self.enabled and thread is not None and thread.is_alive())

    def bind_refresh(self, callback: Callable[[], None] | None) -> None:
        self._on_change = callback

    def set_stop_callback(self, callback: Callable[[], None] | None) -> None:
        pending = False
        with self._lock:
            self._stop_callback = callback
            pending = bool(callback is not None and self._stop_requested)
            if pending:
                self._stop_requested = False
        if pending and callback is not None:
            callback()

    def start(self) -> None:
        if not self.enabled or self.listening:
            return
        self._stop_event.clear()
        self._reader_ready.clear()
        self._thread = threading.Thread(target=self._keyboard_loop, name="devtool-tui-keys", daemon=True)
        self._thread.start()
        # Avoid losing the first shortcut to tty.setcbreak(TCSAFLUSH). Wait for
        # the reader to finish terminal setup before returning to the caller.
        self._reader_ready.wait(timeout=0.5)

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._thread = None

    def append_line(self, line: str) -> None:
        if not self.enabled:
            return
        clean = _ANSI_ESCAPE_RE.sub("", str(line or "")).replace("\r", "\n")
        values = [part.rstrip() for part in clean.splitlines() if part.strip()]
        if not values:
            return
        with self._lock:
            # Preserve the reader's viewport while output continues to arrive.
            # Offset zero means follow the newest output; once the user scrolls
            # up, newly appended lines increase the offset so the text under
            # inspection does not jump away.
            if self._scroll_offset > 0:
                self._scroll_offset += len(values)
            self._lines.extend(values)
            self._scroll_offset = min(self._scroll_offset, max(0, len(self._lines) - 1))
        if self.mode in {TuiDisplayMode.VERBOSE, TuiDisplayMode.LOGS}:
            self._notify_change()

    def lines(self, *, limit: int | None = None) -> tuple[str, ...]:
        with self._lock:
            values = tuple(self._lines)
        if limit is None or limit <= 0:
            return values
        return values[-limit:]

    @property
    def scroll_offset(self) -> int:
        """Number of captured source lines between the viewport and live tail."""

        with self._lock:
            return int(self._scroll_offset)

    @property
    def following(self) -> bool:
        return self.scroll_offset == 0

    def set_page_size(self, value: int) -> None:
        with self._lock:
            self._page_size = max(3, int(value))

    def scroll(self, delta: int) -> bool:
        """Move the log viewport; positive values move toward older output."""

        with self._lock:
            before = self._scroll_offset
            maximum = max(0, len(self._lines) - 1)
            self._scroll_offset = min(maximum, max(0, before + int(delta)))
            changed = self._scroll_offset != before
        if changed:
            self._notify_change()
        return changed

    def jump_oldest(self) -> bool:
        with self._lock:
            before = self._scroll_offset
            self._scroll_offset = max(0, len(self._lines) - self._page_size)
            changed = self._scroll_offset != before
        if changed:
            self._notify_change()
        return changed

    def jump_latest(self) -> bool:
        with self._lock:
            before = self._scroll_offset
            self._scroll_offset = 0
            changed = before != 0
        if changed:
            self._notify_change()
        return changed

    def handle_key(self, key: str) -> bool:
        """Apply one shortcut. Returns True when the key was consumed."""

        raw = str(key or "")
        if not raw:
            return False
        normalized = raw.lower()
        changed = False
        callback: Callable[[], None] | None = None
        with self._lock:
            current = self.mode
            if raw == "\x1b":
                if current in {TuiDisplayMode.HELP, TuiDisplayMode.CONFIRM_STOP}:
                    self.mode = self._previous_mode
                else:
                    self.mode = TuiDisplayMode.COMPACT
                changed = self.mode != current
            elif normalized == "c":
                self.mode = TuiDisplayMode.COMPACT
                changed = self.mode != current
            elif normalized == "l":
                self.mode = TuiDisplayMode.LOGS
                changed = self.mode != current
            elif normalized == "v":
                self.mode = TuiDisplayMode.COMPACT if current == TuiDisplayMode.VERBOSE else TuiDisplayMode.VERBOSE
                changed = self.mode != current
            elif current in {TuiDisplayMode.VERBOSE, TuiDisplayMode.LOGS} and normalized in {"k", "up"}:
                step = 1
                before = self._scroll_offset
                self._scroll_offset = min(max(0, len(self._lines) - 1), before + step)
                changed = self._scroll_offset != before
            elif current in {TuiDisplayMode.VERBOSE, TuiDisplayMode.LOGS} and normalized in {"j", "down"}:
                before = self._scroll_offset
                self._scroll_offset = max(0, before - 1)
                changed = self._scroll_offset != before
            elif current in {TuiDisplayMode.VERBOSE, TuiDisplayMode.LOGS} and normalized in {"u", "pageup"}:
                before = self._scroll_offset
                maximum = max(0, len(self._lines) - 1)
                self._scroll_offset = min(maximum, before + max(1, self._page_size - 1))
                changed = self._scroll_offset != before
            elif current in {TuiDisplayMode.VERBOSE, TuiDisplayMode.LOGS} and normalized in {"d", "pagedown"}:
                before = self._scroll_offset
                self._scroll_offset = max(0, before - max(1, self._page_size - 1))
                changed = self._scroll_offset != before
            elif current in {TuiDisplayMode.VERBOSE, TuiDisplayMode.LOGS} and raw == "g":
                before = self._scroll_offset
                self._scroll_offset = max(0, len(self._lines) - self._page_size)
                changed = self._scroll_offset != before
            elif current in {TuiDisplayMode.VERBOSE, TuiDisplayMode.LOGS} and raw in {"G", "END"}:
                before = self._scroll_offset
                self._scroll_offset = 0
                changed = before != 0
            elif current in {TuiDisplayMode.VERBOSE, TuiDisplayMode.LOGS} and raw == "HOME":
                before = self._scroll_offset
                self._scroll_offset = max(0, len(self._lines) - self._page_size)
                changed = self._scroll_offset != before
            elif raw == "?":
                if current == TuiDisplayMode.HELP:
                    self.mode = self._previous_mode
                else:
                    self._previous_mode = current if current != TuiDisplayMode.CONFIRM_STOP else TuiDisplayMode.COMPACT
                    self.mode = TuiDisplayMode.HELP
                changed = self.mode != current
            elif normalized == "q":
                if current == TuiDisplayMode.CONFIRM_STOP:
                    callback = self._stop_callback
                    if callback is None:
                        self._stop_requested = True
                    self.mode = TuiDisplayMode.COMPACT
                    changed = True
                else:
                    self._previous_mode = current
                    self.mode = TuiDisplayMode.CONFIRM_STOP
                    changed = True
            else:
                return False
        if changed:
            self._notify_change()
        if callback is not None:
            callback()
        return True

    def shortcut_footer(self, width: int | None = None) -> Text | None:
        if not self.enabled:
            return None
        available = int(width or getattr(self.console, "width", 80) or 80)
        if available < 46:
            label = "v/l view · ? keys · q stop"
        elif available < 72:
            label = "v verbose · l logs · c compact · ? keys · q stop"
        else:
            label = "[v] verbose  [l] logs  [c] compact  [?] help  [q] stop"
        return Text(label, style="dim", no_wrap=True, overflow="ellipsis")

    def _notify_change(self) -> None:
        callback = self._on_change
        if callback is not None:
            try:
                callback()
            except Exception:
                pass

    def _keyboard_loop(self) -> None:
        if os.name == "nt":
            self._windows_keyboard_loop()
        else:
            self._posix_keyboard_loop()

    def _posix_keyboard_loop(self) -> None:
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            original = termios.tcgetattr(fd)
        except (AttributeError, OSError, ValueError, termios.error):
            self._reader_ready.set()
            return
        try:
            # cbreak keeps signal processing (notably Ctrl-C) enabled while
            # allowing single-key shortcuts without waiting for Enter.
            tty.setcbreak(fd)
            self._reader_ready.set()
            while not self._stop_event.is_set():
                try:
                    ready, _, _ = select.select([fd], [], [], 0.10)
                except (OSError, ValueError):
                    return
                if not ready:
                    continue
                try:
                    data = os.read(fd, 1)
                except OSError:
                    return
                if not data:
                    continue
                key = data.decode(errors="ignore")
                if data == b"\x1b":
                    # Decode common ANSI navigation sequences without making a
                    # bare Escape key feel sluggish. Termux, tmux, and most
                    # POSIX terminals emit these for arrows/PageUp/PageDown.
                    tail = bytearray()
                    for _ in range(5):
                        try:
                            extra_ready, _, _ = select.select([fd], [], [], 0.008)
                        except (OSError, ValueError):
                            break
                        if not extra_ready:
                            break
                        try:
                            chunk = os.read(fd, 1)
                        except OSError:
                            break
                        if not chunk:
                            break
                        tail.extend(chunk)
                        if bytes(tail).endswith((b"A", b"B", b"H", b"F", b"~")):
                            break
                    sequence = b"\x1b" + bytes(tail)
                    key = {
                        b"\x1b[A": "UP",
                        b"\x1b[B": "DOWN",
                        b"\x1b[5~": "PAGEUP",
                        b"\x1b[6~": "PAGEDOWN",
                        b"\x1b[H": "HOME",
                        b"\x1b[F": "END",
                        b"\x1bOH": "HOME",
                        b"\x1bOF": "END",
                    }.get(sequence, "\x1b")
                self.handle_key(key)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, original)
            except (OSError, termios.error):
                pass

    def _windows_keyboard_loop(self) -> None:
        try:
            import msvcrt
        except ImportError:
            self._reader_ready.set()
            return
        self._reader_ready.set()
        while not self._stop_event.wait(0.05):
            if not msvcrt.kbhit():
                continue
            key = msvcrt.getwch()
            if key in {"\x00", "\xe0"}:
                code = msvcrt.getwch() if msvcrt.kbhit() else ""
                mapped = {
                    "H": "UP",
                    "P": "DOWN",
                    "I": "PAGEUP",
                    "Q": "PAGEDOWN",
                    "G": "HOME",
                    "O": "END",
                }.get(code)
                if mapped:
                    self.handle_key(mapped)
                continue
            self.handle_key(key)


@dataclass(frozen=True, slots=True)
class ThreeLineProgressState:
    completed: int = 0
    total: int | None = None
    unit_label: str = "tasks"
    unit_short: str = ""
    detail: str = "Working"
    detail_root: str = ""
    detail_prefix_full: str = ""
    detail_prefix_compact: str = ""
    extra_full: str = ""
    extra_compact: str = ""
    extra_tiny: str = ""
    started_at: float = 0.0
    returncode: int | None = None
    phase_index: int | None = None
    phase_total: int | None = None
    phase_label: str = ""
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    tests_total: int = 0
    warnings: int = 0
    errors: int = 0


def human_command_label(value: str) -> str:
    """Return a compact human label for internal command/phase names."""

    raw = str(value or "").strip()
    if not raw:
        return "Command"
    known = {
        "python-environment": "Python environment",
        "python-source-audit": "Python source audit",
        "dependency-check": "Dependency check",
        "prepare-dist": "Prepare dist",
        "package-check": "Package check",
        "restore:test-tools": "Test tools",
        "artifact-validate-script": "Artifact validation",
    }
    normalized = raw.lower().replace("_", "-")
    if normalized in known:
        return known[normalized]
    return re.sub(r"[-_]+", " ", raw).strip().capitalize()


def compact_display_target(value: str, *, cwd: str = "", max_parts: int = 2) -> str:
    """Compact a path for terminal chrome while preserving useful identity."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    # Named targets are more useful as-is than attempting to interpret them as
    # filesystem paths.
    if not raw.startswith(("/", "~")) and os.sep not in raw:
        return raw
    try:
        path = Path(raw).expanduser()
        if cwd:
            current = Path(cwd).expanduser()
            try:
                relative = path.relative_to(current)
                if not relative.parts:
                    return current.name or str(current)
                if len(relative.parts) <= max_parts:
                    return "/".join(relative.parts)
                return "…/" + "/".join(relative.parts[-max_parts:])
            except ValueError:
                pass
        home = Path.home()
        try:
            relative = path.relative_to(home)
            parts = relative.parts
            if len(parts) <= max_parts:
                return "~/" + "/".join(parts)
            return "~/…/" + "/".join(parts[-max_parts:])
        except ValueError:
            pass
        parts = path.parts
        if len(parts) <= max_parts + 1:
            return str(path)
        return "…/" + "/".join(parts[-max_parts:])
    except (OSError, RuntimeError, ValueError):
        return raw


def compact_detail_text(value: str, *, cwd: str = "") -> str:
    """Compact absolute filesystem paths embedded in one live-output line."""

    raw = " ".join(str(value or "").split())
    if not raw:
        return ""

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        # Keep punctuation that commonly terminates a path out of the Path
        # object so diagnostics such as ``file.py:42`` remain readable.
        suffix = ""
        while token and token[-1] in ",;:.)":
            suffix = token[-1] + suffix
            token = token[:-1]
        if not token or token == "/":
            return match.group(0)
        return compact_display_target(token, cwd=cwd, max_parts=3) + suffix

    return _ABSOLUTE_PATH_RE.sub(replace, raw)


def compact_command_label(value: str, *, max_length: int = 24) -> str:
    """Return a short command label that survives narrow phone terminals."""

    full = human_command_label(value)
    normalized = str(value or "").strip().lower().replace("_", "-")
    known = {
        "android-validation-explicit-tasks": "Android tasks",
        "android-validation-build": "Android build",
        "android-validation-test": "Android tests",
        "android-validation-lint": "Android lint",
        "python-environment": "Python env",
        "python-source-audit": "Source audit",
        "dependency-check": "Dependencies",
        "prepare-dist": "Prepare dist",
        "package-check": "Package check",
        "artifact-validate-script": "Artifact check",
        "restore:test-tools": "Test tools",
    }
    compact = known.get(normalized, full)
    if len(compact) <= max_length:
        return compact
    words = compact.split()
    removable = {"validation", "explicit", "command", "phase"}
    reduced = " ".join(word for word in words if word.lower() not in removable) or compact
    if len(reduced) <= max_length:
        return reduced
    if max_length <= 1:
        return "…"
    return reduced[: max_length - 1].rstrip() + "…"


def _format_elapsed(seconds: float) -> str:
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _indicator(state: ThreeLineProgressState, now: float) -> str:
    if state.returncode is not None:
        return "✓" if state.returncode == 0 else "✗"
    started = state.started_at or now
    index = int(max(0.0, now - started) * 8.0) % len(_SPINNER_FRAMES)
    return _SPINNER_FRAMES[index]


def _left_truncate(value: str, width: int) -> str:
    clean = " ".join(str(value or "").split())
    if width <= 0:
        return ""
    if cell_len(clean) <= width:
        return clean
    if width == 1:
        return "…"
    tail = set_cell_size(clean, width - 1)
    # set_cell_size truncates from the right. Preserve the newest/right-most
    # detail by trimming codepoints from the left until it fits in cells.
    tail = clean
    while tail and cell_len(tail) > width - 1:
        tail = tail[1:]
    return "…" + tail


def _phase_percent(state: ThreeLineProgressState) -> float | None:
    if state.total is None or state.total <= 0:
        if state.returncode == 0:
            return 100.0
        return None
    return max(0.0, min(100.0, float(state.completed or 0) * 100.0 / float(state.total)))


def _status_line(state: ThreeLineProgressState, width: int, now: float) -> str:
    completed = max(0, int(state.completed or 0))
    timer = _format_elapsed(now - (state.started_at or now))
    indicator = _indicator(state, now)
    unit_full = str(state.unit_label or "items").strip()
    unit_short = str(state.unit_short or "").strip()
    percent = _phase_percent(state)

    if state.total is None or state.total <= 0:
        if state.returncode is None:
            count = "Working"
        elif state.returncode == 0:
            count = "Done"
        else:
            count = "Failed"
        unit_full = ""
        unit_short = ""
        percent_text = ""
    elif unit_full.lower() in {"percent", "%"} and state.total == 100:
        count = f"{completed}%"
        unit_full = ""
        unit_short = ""
        percent_text = ""
    else:
        total = str(max(0, int(state.total)))
        count = f"{completed}/{total}"
        percent_text = f"{percent:.0f}%" if percent is not None else ""

    def base(unit: str, extra: str = "") -> str:
        pieces = [f"{indicator} {count}"]
        if unit:
            pieces[-1] += f" {unit}"
        if percent_text:
            pieces.append(percent_text)
        if extra:
            pieces.append(extra)
        pieces.append(timer)
        return " • ".join(pieces)

    candidates: list[str] = []
    if state.extra_full:
        candidates.append(base(unit_full, state.extra_full))
    else:
        candidates.append(base(unit_full))

    compact_unit = unit_short or unit_full
    compact_extra = state.extra_compact or state.extra_full
    if compact_extra:
        candidates.append(base(compact_unit, compact_extra))
    else:
        candidates.append(base(compact_unit))

    tiny_extra = state.extra_tiny or state.extra_compact or state.extra_full
    if tiny_extra:
        candidates.append(base("", tiny_extra))
    candidates.append(base(""))
    candidates.append(f"{indicator}{count} {timer}")

    for candidate in candidates:
        if cell_len(candidate) <= width:
            return candidate
    return _left_truncate(candidates[-1], width)


def _bar_line(state: ThreeLineProgressState, width: int, now: float) -> str:
    width = max(1, int(width))
    total = state.total
    if total is None and state.returncode == 0:
        return "█" * width
    if total is not None and total > 0:
        ratio = max(0.0, min(1.0, float(state.completed or 0) / float(total)))
        filled = int(round(width * ratio))
        if 0.0 < ratio < 1.0:
            filled = max(1, min(width - 1 if width > 1 else 1, filled))
        return "█" * filled + "░" * (width - filled)

    pulse = max(1, min(width, max(3, width // 5)))
    travel = max(1, width - pulse + 1)
    started = state.started_at or now
    position = int(max(0.0, now - started) * 8.0) % travel
    chars = ["░"] * width
    for index in range(position, min(width, position + pulse)):
        chars[index] = "█"
    return "".join(chars)


def _overall_percent(state: ThreeLineProgressState) -> float | None:
    if state.phase_index is None or not state.phase_total or state.phase_total <= 0:
        return None
    local = _phase_percent(state)
    fraction = 0.0 if local is None else local / 100.0
    completed_phases = max(0, min(state.phase_total, state.phase_index - 1))
    return max(0.0, min(100.0, (completed_phases + fraction) * 100.0 / state.phase_total))


def _overall_bar(percent: float, width: int) -> str:
    safe_width = max(1, int(width))
    ratio = max(0.0, min(1.0, percent / 100.0))
    filled = int(round(safe_width * ratio))
    if 0.0 < ratio < 1.0:
        filled = max(1, min(safe_width - 1 if safe_width > 1 else 1, filled))
    return "█" * filled + "░" * (safe_width - filled)


def render_three_line_progress(
    state: ThreeLineProgressState,
    width: int,
    *,
    now: float | None = None,
) -> tuple[str, str, str]:
    """Return the exact three phase-progress lines for tests/custom renderers."""

    current = time.monotonic() if now is None else float(now)
    safe_width = max(1, int(width))
    prefix = state.detail_prefix_full
    if state.detail_prefix_compact and safe_width < 56:
        prefix = state.detail_prefix_compact
    raw_detail = compact_detail_text(state.detail or "Working", cwd=state.detail_root)
    if prefix:
        available = max(1, safe_width - cell_len(prefix) - 1)
        detail = f"{prefix} {_left_truncate(raw_detail, available)}"
    else:
        detail = _left_truncate(raw_detail, safe_width)
    return (
        _status_line(state, safe_width, current),
        _bar_line(state, safe_width, current),
        detail,
    )


def _overall_header(state: ThreeLineProgressState, percent: float, width: int) -> str:
    """Keep phase position and percent visible before spending width on labels."""

    full = human_command_label(state.phase_label) if state.phase_label else "Current phase"
    compact = compact_command_label(state.phase_label or full, max_length=max(10, min(22, width // 2)))
    candidates = [
        f"Step {state.phase_index} of {state.phase_total} · {full} · {percent:.0f}% overall",
        f"{state.phase_index}/{state.phase_total} · {compact} · {percent:.0f}% overall",
        f"{state.phase_index}/{state.phase_total} · {compact} · {percent:.0f}%",
        f"{state.phase_index}/{state.phase_total} · {percent:.0f}% overall",
    ]
    for candidate in candidates:
        if cell_len(candidate) <= width:
            return candidate
    return set_cell_size(candidates[-1], width)


def active_panel_title(command: str, target: str, *, icon: str, width: int) -> Text:
    """Build a panel title that degrades cleanly instead of being cut by Rich."""

    available = max(8, int(width) - 8)
    label = human_command_label(command)
    compact = compact_command_label(command, max_length=max(8, available - 4))
    candidates = []
    if target:
        candidates.append(f"{icon} {label} · {target}")
        candidates.append(f"{icon} {compact} · {target}")
    candidates.extend((f"{icon} {label}", f"{icon} {compact}"))
    chosen = next((item for item in candidates if cell_len(item) <= available), candidates[-1])
    if cell_len(chosen) > available:
        chosen = set_cell_size(chosen, max(1, available - 1)).rstrip() + "…"
    style = "bold"
    text = Text(" ")
    text.append(chosen, style=style)
    text.append(" ")
    return text

def completion_line(
    *,
    command: str,
    returncode: int,
    started_at: float,
    snapshot: dict[str, object] | None = None,
    now: float | None = None,
) -> Text:
    """Render one durable row after a transient active progress panel exits."""

    data = dict(snapshot or {})
    current = time.monotonic() if now is None else float(now)
    style = "green" if returncode == 0 else "bold red"
    icon = "✓" if returncode == 0 else "✗"
    label = human_command_label(command)
    line = Text()
    line.append(f"{icon} ", style=style)
    line.append(label, style="bold")

    tests_total = int(data.get("tests_total") or 0)
    tests_passed = int(data.get("tests_passed") or 0)
    tests_failed = int(data.get("tests_failed") or 0)
    tests_skipped = int(data.get("tests_skipped") or 0)
    if tests_total or tests_passed or tests_failed or tests_skipped:
        line.append(" · ", style="dim")
        line.append(f"{tests_passed} passed", style="green")
        if tests_failed:
            line.append(f" · {tests_failed} failed", style="bold red")
        if tests_skipped:
            line.append(f" · {tests_skipped} skipped", style="yellow")
    warnings = int(data.get("warnings") or 0)
    errors = int(data.get("errors") or 0)
    if warnings or errors:
        line.append(" · ", style="dim")
        if warnings:
            line.append(f"⚠ {warnings}", style="yellow")
        if errors:
            if warnings:
                line.append(" ", style="dim")
            line.append(f"✗ {errors}", style="red")
    line.append(" · ", style="dim")
    line.append(_format_elapsed(current - started_at), style="dim")
    return line


class ThreeLineProgressBody:
    """Live Rich renderable backed by a state callback and optional TUI inspector."""

    def __init__(
        self,
        state_provider: Callable[[], ThreeLineProgressState],
        *,
        inspection: TuiInspectionController | None = None,
    ) -> None:
        self._state_provider = state_provider
        self.inspection = inspection

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        inspection = self.inspection
        mode = inspection.mode if inspection is not None and inspection.enabled else TuiDisplayMode.COMPACT

        if mode == TuiDisplayMode.HELP:
            yield self._help_panel()
            return
        if mode == TuiDisplayMode.CONFIRM_STOP:
            yield self._stop_panel()
            return
        if mode == TuiDisplayMode.LOGS:
            yield self._log_panel(console, options, include_progress=False)
            return
        if mode == TuiDisplayMode.VERBOSE:
            yield self._log_panel(console, options, include_progress=True)
            return

        yield from self._render_compact(console, options)
        if inspection is not None:
            footer = inspection.shortcut_footer(options.max_width)
            if footer is not None:
                yield footer

    def _render_compact(self, console: Console, options: ConsoleOptions) -> RenderResult:
        state = self._state_provider()
        now = time.monotonic()
        width = max(1, options.max_width)
        overall = _overall_percent(state)
        if overall is not None:
            header = _overall_header(state, overall, width)
            yield Text(header, style="bold cyan", no_wrap=True, overflow="crop")
            overall_text = Text(no_wrap=True, overflow="crop")
            for char in _overall_bar(overall, width):
                overall_text.append(char, style="cyan" if char == "█" else "dim")
            yield overall_text

        status, bar, detail = render_three_line_progress(state, width, now=now)
        if state.returncode is None:
            status_style = "bold cyan"
            bar_style = "cyan"
        elif state.returncode == 0:
            status_style = "bold green"
            bar_style = "green"
        else:
            status_style = "bold red"
            bar_style = "red"
        yield Text(status, style=status_style, no_wrap=True, overflow="crop")
        bar_text = Text(no_wrap=True, overflow="crop")
        for char in bar:
            bar_text.append(char, style=bar_style if char == "█" else "dim")
        yield bar_text
        yield Text(detail, style="dim", no_wrap=True, overflow="crop")

        if state.tests_total or state.tests_passed or state.tests_failed or state.tests_skipped:
            tests = Text()
            tests.append(f"{state.tests_passed} passed", style="green")
            tests.append(f" · {state.tests_failed} failed", style="bold red" if state.tests_failed else "dim")
            if state.tests_skipped:
                tests.append(f" · {state.tests_skipped} skipped", style="yellow")
            if state.tests_total:
                remaining = max(0, state.tests_total - state.tests_passed - state.tests_failed - state.tests_skipped)
                tests.append(f" · {remaining} remaining", style="dim")
            tests.no_wrap = True
            tests.overflow = "ellipsis"
            yield tests

        if state.warnings or state.errors:
            diagnostics = Text()
            if state.warnings:
                diagnostics.append(f"⚠ {state.warnings} warning{'s' if state.warnings != 1 else ''}", style="yellow")
            if state.errors:
                if state.warnings:
                    diagnostics.append(" · ", style="dim")
                diagnostics.append(f"✗ {state.errors} error{'s' if state.errors != 1 else ''}", style="red")
            yield diagnostics

    def _log_panel(self, console: Console, options: ConsoleOptions, *, include_progress: bool) -> Panel:
        inspection = self.inspection
        assert inspection is not None
        width = max(1, int(options.max_width or 1))
        max_height = max(6, int(options.max_height or 24))

        # Verbose deliberately reserves room for live progress. Logs-only uses
        # almost the entire terminal as a reader, which is the practical reason
        # to keep `l` separate from `v`.
        reserve = 10 if include_progress else 4
        row_budget = max(3, max_height - reserve)
        content_width = max(8, width - 4)
        inspection.set_page_size(row_budget)

        all_lines = inspection.lines()
        total = len(all_lines)
        offset = min(inspection.scroll_offset, max(0, total - 1))
        end = max(0, total - offset)
        selected: list[tuple[int, str]] = []
        used_rows = 0
        for index in range(end - 1, -1, -1):
            value = all_lines[index]
            probe = Text(value, no_wrap=False, overflow="fold")
            visual_rows = max(1, len(probe.wrap(console, content_width, overflow="fold", no_wrap=False)))
            if selected and used_rows + visual_rows > row_budget:
                break
            selected.append((index, value))
            used_rows += visual_rows
            if used_rows >= row_budget:
                break
        selected.reverse()

        log_rows: list[Text] = []
        if selected:
            for _, value in selected:
                lowered = value.lower()
                style = "red" if "error" in lowered or "failed" in lowered else ("yellow" if "warning" in lowered else "")
                # Fold instead of ellipsizing. Long Gradle paths and exception
                # messages remain readable in 40-column Termux portrait panes.
                log_rows.append(Text(value, style=style, no_wrap=False, overflow="fold"))
        else:
            log_rows.append(Text("Waiting for command output…", style="dim", overflow="fold"))

        rows: list[object] = []
        if include_progress:
            rows.append(ThreeLineProgressBody(self._state_provider))
            rows.append(Text("Live output", style="bold"))
        rows.extend(log_rows)

        if total:
            first = selected[0][0] + 1 if selected else 0
            last = selected[-1][0] + 1 if selected else 0
            position = "follow" if inspection.following else f"↑{inspection.scroll_offset}"
            status = f"lines {first}-{last}/{total} · {position}"
            rows.append(Text(status, style="dim", no_wrap=True, overflow="ellipsis"))

        view_hint = "l logs" if include_progress else "v verbose"
        if width < 46:
            footer = f"{view_hint} · j/k · G live · ?"
        elif width < 72:
            footer = f"{view_hint} · j/k · u/d page · g/G · c compact · ?"
        else:
            footer = f"{view_hint} · j/k scroll · u/d page · g/G ends · c compact · ? help · q stop"
        rows.append(Text(footer, style="dim", no_wrap=True, overflow="ellipsis"))

        if width < 46:
            title = "Verbose" if include_progress else "Logs"
        else:
            title = "Verbose · progress + output" if include_progress else "Logs · scrollable output"
        return Panel(Group(*rows), title=title, border_style="cyan", padding=(0, 1))

    def _help_panel(self) -> Panel:
        width = int(getattr(self.inspection.console if self.inspection is not None else None, "width", 80) or 80)
        if width < 50:
            rows = Group(
                Text("v  verbose + progress", style="bold"),
                Text("l  logs reader"),
                Text("j/k or ↑/↓  scroll"),
                Text("u/d or PgUp/PgDn  page"),
                Text("g/G  oldest/live"),
                Text("c  compact   ?  help"),
                Text("q q  stop", style="yellow"),
            )
            title = "Keys"
        else:
            rows = Group(
                Text("v  Toggle live verbose view (progress + output)", style="bold"),
                Text("l  Open dedicated scrollable log viewer"),
                Text("j/k or ↓/↑  Scroll one output line"),
                Text("d/u or PgDn/PgUp  Scroll one page"),
                Text("g/G or Home/End  Jump to oldest / resume live tail"),
                Text("c  Compact progress view"),
                Text("?  Toggle this help"),
                Text("Esc  Close modal / return compact"),
                Text("q q  Stop the running command (two presses)", style="yellow"),
            )
            title = "TUI shortcuts"
        return Panel(rows, title=title, border_style="cyan", padding=(0, 1))

    @staticmethod
    def _stop_panel() -> Panel:
        return Panel(
            Group(
                Text("Stop the running command?", style="bold yellow"),
                Text("Press q again to stop. Press Esc to cancel.", style="dim"),
            ),
            title="Confirm stop",
            border_style="yellow",
            padding=(0, 1),
        )

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(1, options.max_width)
