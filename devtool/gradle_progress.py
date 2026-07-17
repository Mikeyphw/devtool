"""Gradle lifecycle progress driven by DEVTOOL_EVENT JSON lines."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any, Callable

from rich.console import Console, Group
from rich.errors import LiveError
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, ProgressColumn, SpinnerColumn, Task, TaskID, TextColumn, TimeElapsedColumn
from rich.text import Text

_EVENT_PREFIX = "DEVTOOL_EVENT "
_EVENT_KINDS = {
    "task-graph", "task-start", "task-finish", "test-suite-start",
    "test-suite-finish", "test-start", "test-finish", "build-finish",
}
_COUNTER_FIELDS = {"sequence", "current", "completed", "total", "passed", "failed", "skipped", "testsTotal", "durationMs"}
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\a]*(?:\a|\x1b\\)")
_TASK_LINE_RE = re.compile(r"^>\s*Task\s+(?P<task>\S+)(?:\s+(?P<outcome>[A-Z][A-Z_-]+))?")
_HEADING_RE = re.compile(r"^==>\s*(?P<heading>.+)")
_LIFECYCLE_PREFIXES = (
    "Calculating task graph",
    "Reusing configuration cache",
    "Configuration cache",
    "BUILD SUCCESSFUL",
    "BUILD FAILED",
)


class GradleTaskCountColumn(ProgressColumn):
    """Render an explicit completed/total task count, including discovery state."""

    def render(self, task: Task) -> Text:
        completed = max(0, int(task.completed or 0))
        if task.total is None:
            return Text(f"{completed}/? tasks", style="progress.data.speed")
        return Text(f"{completed}/{max(0, int(task.total))} tasks", style="progress.data.speed")


@dataclass(slots=True)
class GradleProgressTracker:
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
    task_id: TaskID | None = field(init=False, default=None)
    enabled: bool = field(init=False, default=False)
    update_callback: Callable[[dict[str, Any]], None] | None = None
    detail: str = field(init=False, default="Preparing Gradle task graph")
    completed: int | None = field(init=False, default=0)
    total: int | None = field(init=False, default=None)
    warnings: int = field(init=False, default=0)
    errors: int = field(init=False, default=0)
    tests_passed: int = field(init=False, default=0)
    tests_failed: int = field(init=False, default=0)
    tests_skipped: int = field(init=False, default=0)
    tests_total: int = field(init=False, default=0)
    current_task: str = field(init=False, default="")
    current_test: str = field(init=False, default="")
    returncode: int | None = field(init=False, default=None)
    last_sequence: int = field(init=False, default=0)
    expected_run_id: str = field(init=False, default="")
    rejected_events: int = field(init=False, default=0)
    structured_events_seen: bool = field(init=False, default=False)
    fallback_lines: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.enabled = bool(self.console.is_terminal or self.console.is_interactive)
        self.expected_run_id = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip()

    def __enter__(self) -> "GradleProgressTracker":
        if not self.enabled:
            return self
        self.progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=None, complete_style="cyan", finished_style="green"),
            GradleTaskCountColumn(),
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
            transient=False,
        )
        self.task_id = self.progress.add_task(self._description(), total=None)
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

    @staticmethod
    def parse_event(line: str) -> dict[str, Any] | None:
        text = line.strip()
        if not text.startswith(_EVENT_PREFIX):
            return None
        try:
            payload = json.loads(text[len(_EVENT_PREFIX):])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        kind = str(payload.get("kind") or "")
        if kind not in _EVENT_KINDS:
            return None
        for key in _COUNTER_FIELDS:
            if key not in payload or payload.get(key) is None:
                continue
            try:
                number = int(payload[key])
            except (TypeError, ValueError):
                return None
            if number < 0:
                return None
            payload[key] = number
        total = payload.get("total")
        completed = payload.get("completed")
        if total is not None and completed is not None and completed > total:
            return None
        return payload

    def update_from_line(self, line: str) -> None:
        prefixed = line.strip().startswith(_EVENT_PREFIX)
        event = self.parse_event(line)
        if event is None:
            if prefixed:
                self.rejected_events += 1
                return
            self._update_from_plain_gradle_line(line)
            return
        event_run_id = str(event.get("run_id") or "").strip()
        if self.expected_run_id and event_run_id != self.expected_run_id:
            self.rejected_events += 1
            return
        self.structured_events_seen = True
        sequence = int(event.get("sequence") or 0)
        if sequence and sequence <= self.last_sequence:
            return
        if sequence:
            self.last_sequence = sequence

        kind = str(event.get("kind") or "")
        if kind == "task-graph":
            self.total = int(event.get("total") or 0) or None
            self.detail = f"Gradle task graph ready: {self.total or 0} tasks"
        elif kind == "task-start":
            self.current_task = str(event.get("task") or "")
            self.completed = int(event.get("completed") or 0)
            self.total = int(event.get("total") or 0) or self.total
            self.detail = f"Running {self.current_task or 'Gradle task'}"
        elif kind == "task-finish":
            self.current_task = str(event.get("task") or self.current_task)
            self.completed = int(event.get("completed") or self.completed or 0)
            self.total = int(event.get("total") or 0) or self.total
            outcome = str(event.get("outcome") or "complete")
            self.detail = f"{self.current_task or 'Gradle task'}: {outcome}"
            if outcome == "failed":
                self.errors += 1
        elif kind == "test-start":
            self.current_test = str(event.get("test") or "")
            self.detail = f"Testing {self.current_test or 'test'}"
            self._update_tests(event)
        elif kind in {"test-finish", "test-suite-finish"}:
            self.current_test = str(event.get("test") or event.get("suite") or self.current_test)
            self._update_tests(event)
            self.detail = (
                f"Tests: {self.tests_passed} passed, {self.tests_failed} failed, "
                f"{self.tests_skipped} skipped"
            )
        elif kind == "build-finish":
            self.completed = int(event.get("completed") or self.completed or 0)
            self.total = int(event.get("total") or 0) or self.total
            self._update_tests({
                "passed": event.get("passed"),
                "failed": event.get("failed"),
                "skipped": event.get("skipped"),
                "total": event.get("testsTotal"),
            })
            self.detail = "Gradle build completed" if event.get("success") else "Gradle build failed"
            if not event.get("success"):
                self.errors = max(1, self.errors)
        self._publish(force=kind in {"task-graph", "build-finish"})

    def _update_from_plain_gradle_line(self, line: str) -> None:
        if self.structured_events_seen:
            return
        text = _ANSI_RE.sub("", line).strip()
        if not text:
            return
        match = _TASK_LINE_RE.match(text)
        if match:
            task = match.group("task") or ""
            outcome = (match.group("outcome") or "").lower().replace("_", "-")
            self.current_task = task
            if self.total is None:
                self.completed = (self.completed or 0) + 1
            detail = task or "Gradle task"
            if outcome:
                detail += f": {outcome}"
                if outcome == "failed":
                    self.errors += 1
            self.detail = detail
            self.fallback_lines += 1
            self._publish()
            return

        heading = _HEADING_RE.match(text)
        if heading:
            self.detail = heading.group("heading")
            self.fallback_lines += 1
            self._publish()
            return

        if any(text.startswith(prefix) for prefix in _LIFECYCLE_PREFIXES):
            self.detail = text
            if text.startswith("BUILD FAILED"):
                self.errors = max(1, self.errors)
            self.fallback_lines += 1
            self._publish()
            return

    def _update_tests(self, event: dict[str, Any]) -> None:
        self.tests_passed = int(event.get("passed") or 0)
        self.tests_failed = int(event.get("failed") or 0)
        self.tests_skipped = int(event.get("skipped") or 0)
        self.tests_total = int(event.get("total") or (self.tests_passed + self.tests_failed + self.tests_skipped))

    def finish(self, returncode: int) -> None:
        self.returncode = returncode
        if returncode == 0 and self.total is not None:
            self.completed = self.total
        status = "Gradle completed" if returncode == 0 else f"Gradle failed rc={returncode}"
        if self.tests_total:
            status += (
                f" • tests: {self.tests_passed} passed, {self.tests_failed} failed, "
                f"{self.tests_skipped} skipped"
            )
        self.detail = status
        self._publish(force=True)

    def snapshot(self) -> dict[str, Any]:
        percent = None
        if self.completed is not None and self.total:
            percent = max(0.0, min(100.0, self.completed / self.total * 100.0))
        return {
            "command": self.command_name,
            "target": self.display_target or self.cwd,
            "detail": self.detail,
            "completed": self.completed,
            "total": self.total,
            "percent": percent,
            "warnings": self.warnings,
            "errors": self.errors,
            "unit_label": "tasks",
            "current_task": self.current_task,
            "current_test": self.current_test,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_skipped": self.tests_skipped,
            "tests_total": self.tests_total,
            "event_sequence": self.last_sequence,
            "rejected_events": self.rejected_events,
            "run_id": self.expected_run_id,
        }

    def _publish(self, *, force: bool = False) -> None:
        if self.progress is not None and self.task_id is not None:
            self.progress.update(
                self.task_id,
                completed=float(self.completed or 0),
                total=float(self.total) if self.total is not None else None,
            )
        if self.live is not None:
            self.live.update(self._renderable())
        if self.update_callback is not None:
            snapshot = self.snapshot()
            snapshot["force"] = force
            self.update_callback(snapshot)

    def _description(self) -> str:
        if self.phase_index is not None and self.phase_total:
            return f"{self.command_name} {self.phase_index}/{self.phase_total} · Gradle"
        return f"Gradle {self.command_name}"

    def _renderable(self) -> Panel:
        target = self.display_target or Path(self.cwd).name or "Gradle project"
        if self.returncode is None:
            icon, border, style = "◆", "cyan", "bold cyan"
        elif self.returncode == 0:
            icon, border, style = "✓", "green", "bold green"
        else:
            icon, border, style = "✗", "red", "bold red"
        title = Text()
        title.append(f" {icon} ", style=style)
        title.append("Gradle ", style="bold")
        title.append("• ", style="dim")
        title.append(str(target), style="bold white")
        detail = Text("  " + self.detail, style="dim", overflow="fold")
        body = Group(self.progress, detail) if self.progress is not None else detail
        return Panel(body, title=title, border_style=border, padding=(0, 1))
