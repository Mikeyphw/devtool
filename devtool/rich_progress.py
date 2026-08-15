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

from dataclasses import dataclass
from pathlib import Path
import os
import re
import time
from typing import Callable

from rich.console import Console, ConsoleOptions, RenderResult
from rich.cells import cell_len, set_cell_size
from rich.measure import Measurement
from rich.text import Text

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w:/])/(?:[^\s\[\]{}<>\"'`]+)")


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
    """Live Rich renderable backed by a state callback."""

    def __init__(self, state_provider: Callable[[], ThreeLineProgressState]) -> None:
        self._state_provider = state_provider

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
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

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(1, options.max_width)
