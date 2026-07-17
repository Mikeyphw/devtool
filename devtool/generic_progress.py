from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable

from rich.console import Console, Group
from rich.errors import LiveError
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, ProgressColumn, SpinnerColumn, Task, TextColumn, TimeElapsedColumn
from rich.text import Text


_FRACTION_RE = re.compile(r"(?:^|[\[(\s])(?P<completed>\d+)\s*/\s*(?P<total>\d+)(?:[\])\s]|$)")
_PERCENT_RE = re.compile(r"(?<!\d)(?P<percent>\d{1,3})%")
_MAVEN_REACTOR_RE = re.compile(r"\bBuilding\s+.+?\s+(?P<completed>\d+)\s*/\s*(?P<total>\d+)\b", re.IGNORECASE)


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
    progress: Progress | None = field(init=False, default=None)
    live: Live | None = field(init=False, default=None)
    task_id: int | None = field(init=False, default=None)
    enabled: bool = field(init=False, default=False)
    update_callback: Callable[[dict[str, Any]], None] | None = None
    detail: str = field(init=False, default="Starting")
    returncode: int | None = field(init=False, default=None)
    last_line: str = field(init=False, default="")
    completed: int | None = field(init=False, default=None)
    total: int | None = field(init=False, default=None)
    percent: float | None = field(init=False, default=None)
    unit_label: str = field(init=False, default="tasks")

    def __post_init__(self) -> None:
        self.enabled = bool(self.console.is_terminal or self.console.is_interactive)

    def __enter__(self) -> "GenericProgressTracker":
        if not self.enabled:
            return self
        self.progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.description}"),
            TextColumn("[dim]{task.fields[phase_label]}[/dim]"),
            BarColumn(bar_width=None),
            GenericCountColumn(),
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
            transient=False,
        )
        self.task_id = self.progress.add_task(self._description(), total=None, phase_label=self._phase_label())
        self.live = Live(
            self._renderable(),
            console=self.console,
            refresh_per_second=8,
            transient=False,
            vertical_overflow="visible",
        )
        try:
            self.live.start()
        except LiveError:
            self.live = None
            self.progress = None
            self.task_id = None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self.live is not None:
            self.live.stop()

    def update_from_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        self.last_line = text
        self.detail = text
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

    def snapshot(self) -> dict[str, Any]:
        return {
            "command": self.command_name,
            "target": self.display_target or self.cwd,
            "detail": self.detail,
            "phase_name": self._phase_label(),
            "completed": self.completed,
            "total": self.total,
            "percent": self.percent,
            "warnings": 0,
            "errors": 0,
            "unit_label": self.unit_label,
            "returncode": self.returncode,
            "last_line": self.last_line,
        }

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
        if match:
            value = max(0, min(100, int(match.group("percent"))))
            self.percent = float(value)
            self.completed = value
            self.total = 100
            self.unit_label = "percent"

    def _publish(self, *, force: bool = False) -> None:
        if self.progress is not None and self.task_id is not None:
            self.progress.update(
                self.task_id,
                phase_label=self._phase_label(),
                completed=float(self.completed or 0),
                total=float(self.total) if self.total is not None else None,
            )
        if self.live is not None:
            self.live.update(self._renderable())
        if self.update_callback is not None:
            self.update_callback(self.snapshot())

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

    def _renderable(self) -> Panel:
        target = self.display_target or self.cwd or "command"
        if self.returncode is None:
            border = "cyan"
            title_style = "bold cyan"
            icon = "◆"
        elif self.returncode == 0:
            border = "green"
            title_style = "bold green"
            icon = "✓"
        else:
            border = "red"
            title_style = "bold red"
            icon = "✗"
        title = Text()
        title.append(f" {icon} ", style=title_style)
        title.append(self.command_name or "command", style="bold")
        title.append(" • ", style="dim")
        title.append(str(target), style="bold white")
        detail = Text("  " + (self.detail or "Running"), style="dim", overflow="fold")
        body = Group(self.progress, detail) if self.progress is not None else detail
        return Panel(body, title=title, border_style=border, padding=(0, 1))
