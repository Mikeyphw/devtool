from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable

from rich.console import Console
from rich.errors import LiveError
from rich.live import Live
from rich.panel import Panel
from rich.progress import ProgressColumn, Task
from rich.text import Text

from .rich_progress import (
    ThreeLineProgressBody,
    ThreeLineProgressState,
    TuiInspectionController,
    active_panel_title,
    compact_display_target,
    completion_line,
)
from .tui_icons import activity_icon


_FRACTION_RE = re.compile(r"(?:^|[\[(\s])(?P<completed>\d+)\s*/\s*(?P<total>\d+)(?:[\])\s]|$)")
_PERCENT_RE = re.compile(r"(?<!\d)(?P<percent>\d{1,3})%")
_MAVEN_REACTOR_RE = re.compile(r"\bBuilding\s+.+?\s+(?P<completed>\d+)\s*/\s*(?P<total>\d+)\b", re.IGNORECASE)
_PYTEST_COLLECTED_RE = re.compile(r"\bcollected\s+(?P<total>\d+)\s+items?\b", re.IGNORECASE)
_PYTEST_PROGRESS_RE = re.compile(r"(?P<body>.*?)\[\s*(?P<percent>\d{1,3})%\]\s*$")
_PYTEST_MARKS_RE = re.compile(r"(?P<marks>[.FfEsxX]+)\s*$")


class GenericCountColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        if task.total is None:
            return Text("?/ ?", style="progress.data.speed")
        return Text(f"{int(task.completed or 0)}/{int(task.total)}", style="progress.data.speed")


@dataclass(slots=True)
class GenericProgressTracker:
    argv: list[str]
    cwd: str
    command_name: str
    console: Console
    phase_index: int | None = None
    phase_total: int | None = None
    display_target: str = ""
    target_phase_index: int | None = None
    target_phase_total: int | None = None
    progress_body: ThreeLineProgressBody | None = field(init=False, default=None)
    inspection: TuiInspectionController = field(init=False)
    live: Live | None = field(init=False, default=None)
    enabled: bool = field(init=False, default=False)
    update_callback: Callable[[dict[str, Any]], None] | None = None
    detail: str = field(init=False, default="Starting")
    returncode: int | None = field(init=False, default=None)
    last_line: str = field(init=False, default="")
    completed: int | None = field(init=False, default=None)
    total: int | None = field(init=False, default=None)
    percent: float | None = field(init=False, default=None)
    unit_label: str = field(init=False, default="tasks")
    tests_passed: int = field(init=False, default=0)
    tests_failed: int = field(init=False, default=0)
    tests_skipped: int = field(init=False, default=0)
    tests_total: int = field(init=False, default=0)
    _completion_printed: bool = field(init=False, default=False)
    _started_at: float = field(init=False, default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.enabled = bool(self.console.is_terminal or self.console.is_interactive)
        self.inspection = TuiInspectionController(self.console)

    def __enter__(self) -> "GenericProgressTracker":
        if not self.enabled:
            return self
        self._started_at = time.monotonic()
        self.progress_body = ThreeLineProgressBody(self._progress_state, inspection=self.inspection)
        self.live = Live(
            self._renderable(),
            console=self.console,
            refresh_per_second=8,
            transient=True,
            vertical_overflow="ellipsis",
        )
        try:
            self.live.start()
        except LiveError:
            self.live = None
            self.progress_body = None
        if self.live is not None:
            self.inspection.bind_refresh(self.live.refresh)
            self.inspection.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.inspection.stop()
        self.inspection.bind_refresh(None)
        if self.live is not None:
            self.live.stop()

    def set_stop_callback(self, callback: Callable[[], None] | None) -> None:
        self.inspection.set_stop_callback(callback)

    def update_from_line(self, line: str) -> None:
        self.inspection.append_line(line)
        text = line.strip()
        if not text:
            return
        self.last_line = text
        self.detail = text
        self._update_pytest_counters(text)
        self._update_counters(text)
        self._publish()

    def finish(self, returncode: int) -> None:
        self.returncode = returncode
        status = f"{self.command_name} ok" if returncode == 0 else f"{self.command_name} failed rc={returncode}"
        if self.detail and self.detail != status:
            self.detail = f"{status} • {self.detail}"
        else:
            self.detail = status
        self._publish(force=True)
        self._print_completion_once()

    def snapshot(self) -> dict[str, Any]:
        return {
            "command": self.command_name,
            "target": self.display_target or self.cwd,
            "detail": self.detail,
            "completion_status": self.detail,
            "phase_name": self._phase_label(),
            "completed": self.completed,
            "total": self.total,
            "percent": self.percent,
            "warnings": 0,
            "errors": 0,
            "unit_label": self.unit_label,
            "returncode": self.returncode,
            "last_line": self.last_line,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_skipped": self.tests_skipped,
            "tests_total": self.tests_total,
        }

    def _update_pytest_counters(self, text: str) -> None:
        collected = _PYTEST_COLLECTED_RE.search(text)
        if collected:
            self.tests_total = max(self.tests_total, int(collected.group("total")))
            self.total = self.tests_total
            self.unit_label = "tests"

        progress = _PYTEST_PROGRESS_RE.search(text)
        if not progress:
            return
        marks_match = _PYTEST_MARKS_RE.search(progress.group("body"))
        if marks_match:
            marks = marks_match.group("marks")
            self.tests_passed += marks.count(".") + marks.count("X")
            self.tests_failed += marks.count("F") + marks.count("f") + marks.count("E")
            self.tests_skipped += marks.count("s") + marks.count("x")
        completed = self.tests_passed + self.tests_failed + self.tests_skipped
        if self.tests_total:
            self.completed = min(self.tests_total, completed)
            self.total = self.tests_total
            self.percent = min(100.0, self.completed * 100.0 / self.tests_total)
            self.unit_label = "tests"
        else:
            value = max(0, min(100, int(progress.group("percent"))))
            self.percent = float(value)
            self.completed = value
            self.total = 100
            self.unit_label = "percent"

    def _update_counters(self, text: str) -> None:
        match = _MAVEN_REACTOR_RE.search(text) or _FRACTION_RE.search(text)
        if match:
            completed = max(0, int(match.group("completed")))
            total = max(1, int(match.group("total")))
            if completed <= total:
                self.completed = completed
                self.total = total
                self.percent = min(100.0, completed * 100.0 / total)
                return
        match = _PERCENT_RE.search(text)
        if match and self.unit_label != "tests":
            value = max(0, min(100, int(match.group("percent"))))
            self.percent = float(value)
            if self.total is not None and self.unit_label != "percent":
                # Keep the established determinate unit. A later percentage is
                # another representation of the same work, not a new 0..100
                # unit system.
                self.completed = min(self.total, int(round(self.total * value / 100.0)))
            else:
                self.completed = value
                self.total = 100
                self.unit_label = "percent"

    def _publish(self, *, force: bool = False) -> None:
        if self.live is not None:
            self.live.update(self._renderable())
        if self.update_callback is not None:
            self.update_callback(self.snapshot())

    def _print_completion_once(self) -> None:
        if not self.enabled or self._completion_printed or self.returncode is None:
            return
        self._completion_printed = True
        self.console.print(
            completion_line(
                command=self.command_name,
                returncode=self.returncode,
                started_at=self._started_at,
                snapshot=self.snapshot(),
            )
        )

    def _description(self) -> str:
        if self.phase_index is not None and self.phase_total:
            return f"{self.command_name} {self.phase_index}/{self.phase_total}"
        return self.command_name or "command"

    def _phase_label(self) -> str:
        command = (self.command_name or "").strip().lower().replace("_", "-")
        return {
            "build": "building",
            "clean": "cleaning",
            "lint": "linting",
            "package": "packaging",
            "publish": "publishing",
            "restore": "restoring",
            "run": "running",
            "test": "testing",
        }.get(command, "running command")

    def _progress_state(self) -> ThreeLineProgressState:
        unit = self.unit_label or "tasks"
        short = "tasks" if unit == "tasks" else ("%" if unit == "percent" else unit)
        return ThreeLineProgressState(
            completed=int(self.completed or 0),
            total=self.total,
            unit_label=unit,
            unit_short=short,
            detail=self.detail or "Starting",
            detail_root=self.cwd,
            started_at=self._started_at,
            returncode=self.returncode,
            phase_index=self.phase_index,
            phase_total=self.phase_total,
            phase_label=self.command_name,
            tests_passed=self.tests_passed,
            tests_failed=self.tests_failed,
            tests_skipped=self.tests_skipped,
            tests_total=self.tests_total,
        )

    def _renderable(self) -> Panel:
        target = compact_display_target(self.display_target or self.cwd or "command", cwd=self.cwd)
        if self.returncode is None:
            border = "cyan"
            title_style = "bold cyan"
            icon = activity_icon(command=self.command_name, target=str(target), cwd=self.cwd)
        elif self.returncode == 0:
            border = "green"
            title_style = "bold green"
            icon = "✓"
        else:
            border = "red"
            title_style = "bold red"
            icon = "✗"
        title = active_panel_title(
            self.command_name,
            str(target),
            icon=icon,
            width=self.console.width,
        )
        title.stylize(title_style)
        body = self.progress_body or ThreeLineProgressBody(self._progress_state, inspection=self.inspection)
        return Panel(body, title=title, border_style=border, padding=(0, 1))
