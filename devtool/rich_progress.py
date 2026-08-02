"""Responsive three-line terminal progress rendering.

The renderer deliberately avoids Rich's multi-column ``Progress`` layout.  On
narrow terminals those columns compete for width and can hide the spinner,
bar, task count, or elapsed time.  Here each line has one job:

1. live indicator, exact count, optional graph breakdown, elapsed time;
2. a full-width determinate or indeterminate bar;
3. the current task/detail, left-truncated to preserve the useful suffix.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.text import Text

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


@dataclass(frozen=True, slots=True)
class ThreeLineProgressState:
    completed: int = 0
    total: int | None = None
    unit_label: str = "tasks"
    unit_short: str = ""
    detail: str = "Working"
    detail_prefix_full: str = ""
    detail_prefix_compact: str = ""
    extra_full: str = ""
    extra_compact: str = ""
    extra_tiny: str = ""
    started_at: float = 0.0
    returncode: int | None = None


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
    if len(clean) <= width:
        return clean
    if width == 1:
        return "…"
    return "…" + clean[-(width - 1):]


def _status_line(state: ThreeLineProgressState, width: int, now: float) -> str:
    completed = max(0, int(state.completed or 0))
    total = "?" if state.total is None else str(max(0, int(state.total)))
    count = f"{completed}/{total}"
    timer = _format_elapsed(now - (state.started_at or now))
    indicator = _indicator(state, now)
    unit_full = str(state.unit_label or "items").strip()
    unit_short = str(state.unit_short or "").strip()

    candidates: list[str] = []
    if state.extra_full:
        candidates.append(f"{indicator} {count} {unit_full} • {state.extra_full} • {timer}")
    else:
        candidates.append(f"{indicator} {count} {unit_full} • {timer}")

    compact_unit = unit_short or unit_full
    compact_extra = state.extra_compact or state.extra_full
    if compact_extra:
        candidates.append(f"{indicator} {count} {compact_unit} • {compact_extra} • {timer}")
    else:
        candidates.append(f"{indicator} {count} {compact_unit} • {timer}")

    tiny_extra = state.extra_tiny or state.extra_compact or state.extra_full
    if tiny_extra:
        candidates.append(f"{indicator} {count} • {tiny_extra} • {timer}")
    candidates.append(f"{indicator} {count} • {timer}")
    candidates.append(f"{indicator}{count} {timer}")

    for candidate in candidates:
        if len(candidate) <= width:
            return candidate
    # Extremely small terminals are physically unable to show every field.
    # Preserve the exact count and timer before any decoration.
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

    # Indeterminate pulse while a task graph/total is being discovered.
    pulse = max(1, min(width, max(3, width // 5)))
    travel = max(1, width - pulse + 1)
    started = state.started_at or now
    position = int(max(0.0, now - started) * 8.0) % travel
    chars = ["░"] * width
    for index in range(position, min(width, position + pulse)):
        chars[index] = "█"
    return "".join(chars)


def render_three_line_progress(
    state: ThreeLineProgressState,
    width: int,
    *,
    now: float | None = None,
) -> tuple[str, str, str]:
    """Return the exact three progress lines for tests and custom renderers."""

    current = time.monotonic() if now is None else float(now)
    safe_width = max(1, int(width))
    prefix = state.detail_prefix_full
    if state.detail_prefix_compact and safe_width < 56:
        prefix = state.detail_prefix_compact
    if prefix:
        available = max(1, safe_width - len(prefix) - 1)
        detail = f"{prefix} {_left_truncate(state.detail or 'Working', available)}"
    else:
        detail = _left_truncate(state.detail or "Working", safe_width)
    return (
        _status_line(state, safe_width, current),
        _bar_line(state, safe_width, current),
        detail,
    )


class ThreeLineProgressBody:
    """Live Rich renderable backed by a state callback."""

    def __init__(self, state_provider: Callable[[], ThreeLineProgressState]) -> None:
        self._state_provider = state_provider

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        state = self._state_provider()
        status, bar, detail = render_three_line_progress(state, options.max_width)
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

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(1, options.max_width)
