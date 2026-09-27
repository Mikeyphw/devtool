"""Gradle lifecycle progress driven by DEVTOOL_EVENT JSON lines."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Sequence

from rich.console import Console, Group
from rich.errors import LiveError
from rich.live import Live
from rich.panel import Panel
from rich.progress import ProgressColumn, Task
from rich.text import Text

from .rich_progress import (
    active_panel_title,
    ThreeLineProgressBody,
    ThreeLineProgressState,
    TuiInspectionController,
    compact_display_target,
    completion_line,
    project_live_output_line,
)
from .tui_icons import activity_icon

_EVENT_PREFIX = "DEVTOOL_EVENT "
_EVENT_KINDS = {
    "task-graph", "task-start", "task-finish", "test-suite-start",
    "test-suite-finish", "test-start", "test-finish", "build-finish",
}
_COUNTER_FIELDS = {"sequence", "current", "completed", "total", "passed", "failed", "skipped", "testsTotal", "durationMs"}
_BOOLEAN_FIELDS = {"didWork", "upToDate", "fromCache", "noSource", "incremental"}
_TRUE_STRINGS = {"1", "true", "yes", "y", "on"}
_FALSE_STRINGS = {"0", "false", "no", "n", "off", ""}
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\a]*(?:\a|\x1b\\)")
_TASK_LINE_RE = re.compile(r"^>\s*Task\s+(?P<task>\S+)(?:\s+(?P<outcome>[A-Z][A-Z_-]+))?")
_ACTIONABLE_TASKS_RE = re.compile(r"^(?P<total>\d+)\s+actionable\s+tasks?(?:\s*:.*)?$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^==>\s*(?P<heading>.+)")
_LIFECYCLE_PREFIXES = (
    "Calculating task graph",
    "Reusing configuration cache",
    "Configuration cache",
    "BUILD SUCCESSFUL",
    "BUILD FAILED",
)



def _coerce_event_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if value is None:
        return None
    return None

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
    requested_tasks: Sequence[str] = field(default_factory=tuple)
    progress_body: ThreeLineProgressBody | None = field(init=False, default=None)
    inspection: TuiInspectionController = field(init=False)
    live: Live | None = field(init=False, default=None)
    enabled: bool = field(init=False, default=False)
    update_callback: Callable[[dict[str, Any]], None] | None = None
    structured_event_callback: Callable[[dict[str, Any]], None] | None = None
    detail: str = field(init=False, default="Preparing Gradle environment")
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
    failed_task: str = field(init=False, default="")
    returncode: int | None = field(init=False, default=None)
    last_sequence: int = field(init=False, default=0)
    expected_run_id: str = field(init=False, default="")
    rejected_events: int = field(init=False, default=0)
    structured_events_seen: bool = field(init=False, default=False)
    real_progress_seen: bool = field(init=False, default=False)
    fallback_lines: int = field(init=False, default=0)
    graph_tasks: tuple[str, ...] = field(init=False, default=())
    observed_tasks: set[str] = field(init=False, default_factory=set)
    requested_task_count: int = field(init=False, default=0)
    dependency_task_count: int = field(init=False, default=0)
    requested_duplicate_count: int = field(init=False, default=0)
    requested_duplicate_tasks: dict[str, int] = field(init=False, default_factory=dict)
    task_header_counts: dict[str, int] = field(init=False, default_factory=dict)
    task_resume_counts: dict[str, int] = field(init=False, default_factory=dict)
    task_outcomes: dict[str, str] = field(init=False, default_factory=dict)
    started_tasks: set[str] = field(init=False, default_factory=set)
    completed_tasks: set[str] = field(init=False, default_factory=set)
    structured_task_start_counts: dict[str, int] = field(init=False, default_factory=dict)
    duplicate_execution_start_count: int = field(init=False, default=0)
    duplicate_terminal_header_count: int = field(init=False, default=0)
    _completion_printed: bool = field(init=False, default=False)
    _started_at: float = field(init=False, default_factory=time.monotonic)
    _process_started_at: float | None = field(init=False, default=None)
    _synthetic_stop: threading.Event = field(init=False, default_factory=threading.Event)
    _synthetic_thread: threading.Thread | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.enabled = bool(self.console.is_terminal or self.console.is_interactive)
        self.inspection = TuiInspectionController(self.console)
        self.expected_run_id = str(os.environ.get("DEVTOOL_RUN_ID") or "").strip()
        raw_requested = tuple(str(task).strip() for task in self.requested_tasks if str(task).strip())
        requested_counts = Counter(raw_requested)
        self.requested_duplicate_tasks = {task: count for task, count in requested_counts.items() if count > 1}
        self.requested_duplicate_count = sum(count - 1 for count in self.requested_duplicate_tasks.values())
        self.requested_tasks = tuple(dict.fromkeys(raw_requested))

    def __enter__(self) -> "GradleProgressTracker":
        if not self.enabled or self.live is not None:
            return self
        self._started_at = time.monotonic()
        self._synthetic_stop.clear()
        if self.requested_tasks:
            self.console.print(self._requested_tasks_renderable())
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
            self._synthetic_thread = threading.Thread(
                target=self._synthetic_progress_loop,
                name="devtool-gradle-startup-progress",
                daemon=True,
            )
            self._synthetic_thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self._synthetic_stop.set()
        thread = self._synthetic_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._synthetic_thread = None
        self.inspection.stop()
        self.inspection.bind_refresh(None)
        if self.live is not None:
            self.live.stop()
            self.live = None

    def mark_process_started(self) -> None:
        """Advance startup feedback as soon as the Gradle/chroot child exists."""

        if self._process_started_at is None:
            self._process_started_at = time.monotonic()
        if not self.real_progress_seen and self.returncode is None:
            self._set_synthetic_detail(self._synthetic_detail_for_elapsed(0.0))

    def _synthetic_detail_for_elapsed(self, process_elapsed: float | None) -> str:
        if process_elapsed is None:
            return "Preparing Gradle environment"
        if process_elapsed < 2.5:
            if "--no-daemon" in self.argv:
                return "Starting Gradle process"
            return "Starting Gradle daemon"
        return "Configuring Gradle project"

    def _set_synthetic_detail(self, detail: str) -> None:
        if self.real_progress_seen or self.returncode is not None or self.detail == detail:
            return
        self.detail = detail
        self._publish()

    def _synthetic_progress_loop(self) -> None:
        while not self._synthetic_stop.wait(0.25):
            if self.real_progress_seen or self.returncode is not None:
                return
            process_elapsed = None
            if self._process_started_at is not None:
                process_elapsed = max(0.0, time.monotonic() - self._process_started_at)
            self._set_synthetic_detail(self._synthetic_detail_for_elapsed(process_elapsed))

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
        for key in _BOOLEAN_FIELDS:
            if key not in payload or payload.get(key) is None:
                continue
            coerced = _coerce_event_bool(payload.get(key))
            if coerced is None:
                return None
            payload[key] = coerced
        if "outcome" in payload and payload.get("outcome") is not None:
            payload["outcome"] = str(payload.get("outcome") or "").strip().lower().replace("_", "-")
        total = payload.get("total")
        completed = payload.get("completed")
        if total is not None and completed is not None and completed > total:
            return None
        return payload

    def set_stop_callback(self, callback: Callable[[], None] | None) -> None:
        self.inspection.set_stop_callback(callback)

    def update_from_line(self, line: str) -> bool:
        self.inspection.append_line(line)
        prefixed = line.strip().startswith(_EVENT_PREFIX)
        event = self.parse_event(line)
        if event is None:
            if prefixed:
                self.rejected_events += 1
                return True
            return self._update_from_plain_gradle_line(line)
        event_run_id = str(event.get("run_id") or "").strip()
        if self.expected_run_id and event_run_id != self.expected_run_id:
            self.rejected_events += 1
            return False
        self.structured_events_seen = True
        self.real_progress_seen = True
        self._synthetic_stop.set()
        sequence = int(event.get("sequence") or 0)
        if sequence and sequence <= self.last_sequence:
            return False
        if sequence:
            self.last_sequence = sequence

        kind = str(event.get("kind") or "")
        if kind == "task-graph":
            raw_tasks = event.get("tasks")
            if isinstance(raw_tasks, list):
                self.graph_tasks = tuple(dict.fromkeys(str(task).strip() for task in raw_tasks if str(task).strip()))
            self._merge_total(event.get("total") or len(self.graph_tasks) or None)
            self._update_requested_dependency_counts()
            self.detail = f"Gradle task graph ready: {self.total or 0} tasks"
        elif kind == "task-start":
            self.current_task = str(event.get("task") or "")
            self._record_structured_task_start(self.current_task)
            self._merge_completed(event.get("completed"))
            self._merge_total(event.get("total"))
            self._update_requested_dependency_counts()
            if self.failed_task:
                self.detail = (
                    f"Failed {self.failed_task}; collecting diagnostics in "
                    f"{self.current_task or 'Gradle task'}"
                )
            else:
                self.detail = f"Running {self.current_task or 'Gradle task'}"
        elif kind == "task-finish":
            self.current_task = str(event.get("task") or self.current_task)
            if self.current_task:
                self.started_tasks.add(self.current_task)
                self.completed_tasks.add(self.current_task)
            self._merge_completed(event.get("completed"))
            self._merge_total(event.get("total"))
            self._update_requested_dependency_counts()
            outcome = str(event.get("outcome") or "complete").strip().lower().replace("_", "-")
            if self.current_task:
                self.task_outcomes[self.current_task] = outcome
            self.detail = f"{self.current_task or 'Gradle task'}: {outcome}"
            if outcome == "failed":
                self.errors += 1
                if not self.failed_task:
                    self.failed_task = self.current_task
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
            self._merge_completed(event.get("completed"))
            self._merge_total(event.get("total"))
            self._update_requested_dependency_counts()
            self._update_tests({
                "passed": event.get("passed"),
                "failed": event.get("failed"),
                "skipped": event.get("skipped"),
                "total": event.get("testsTotal"),
            })
            self.detail = "Gradle build completed" if event.get("success") else "Gradle build failed"
            if not event.get("success"):
                self.errors = max(1, self.errors)
        if self.structured_event_callback is not None:
            try:
                self.structured_event_callback(dict(event))
            except Exception:
                pass
        self._publish(force=kind in {"task-graph", "build-finish"})
        # Structured DEVTOOL_EVENT lines are machine telemetry. Keep them in the
        # raw evidence log, but they do not need to be echoed as human console
        # output when the tracker is active.
        return False

    def _record_structured_task_start(self, task: str) -> None:
        if not task:
            return
        count = self.structured_task_start_counts.get(task, 0) + 1
        self.structured_task_start_counts[task] = count
        if count > 1:
            self.duplicate_execution_start_count += 1
        self.started_tasks.add(task)

    def _record_plain_task_header(self, task: str, outcome: str) -> bool:
        """Track a Gradle plain-console task header and decide if it is novel.

        Gradle's ``--console=plain`` repeats a bare ``> Task :path`` header when
        output from a still-running task resumes after interleaved parallel task
        output. Those headers are *not* additional task executions. We count
        them as resume markers and suppress only the duplicate bare header from
        the default human-facing stream. Outcome-bearing headers remain visible.
        """

        if not task:
            return True
        count = self.task_header_counts.get(task, 0) + 1
        self.task_header_counts[task] = count
        first_header = count == 1
        if first_header:
            self.started_tasks.add(task)
        elif not outcome:
            self.task_resume_counts[task] = self.task_resume_counts.get(task, 0) + 1
            return False

        if outcome:
            previous = self.task_outcomes.get(task)
            if previous == outcome:
                self.duplicate_terminal_header_count += 1
                return False
            self.task_outcomes[task] = outcome
            self.completed_tasks.add(task)
        return True

    def _update_from_plain_gradle_line(self, line: str) -> bool:
        text = _ANSI_RE.sub("", line).strip()
        if not text:
            return True
        if self.structured_events_seen:
            # Structured events are authoritative. Hide Gradle's redundant plain
            # task headers from the human stream while preserving all other text.
            return not bool(_TASK_LINE_RE.match(text))
        match = _TASK_LINE_RE.match(text)
        if match:
            self.real_progress_seen = True
            self._synthetic_stop.set()
            task = match.group("task") or ""
            outcome = (match.group("outcome") or "").lower().replace("_", "-")
            surface_line = self._record_plain_task_header(task, outcome)
            self.current_task = task
            first_observation = bool(task) and task not in self.observed_tasks
            if task:
                self.observed_tasks.add(task)
            if self.total is None and first_observation:
                self.completed = len(self.observed_tasks)
            detail = task or "Gradle task"
            if outcome:
                detail += f": {outcome}"
                if outcome == "failed":
                    self.errors += 1
                    if not self.failed_task:
                        self.failed_task = task
            if self.failed_task and task != self.failed_task and outcome != "failed":
                detail = f"Failed {self.failed_task}; collecting diagnostics in {task}"
            self.detail = detail
            self.fallback_lines += 1
            self._publish()
            return surface_line

        summary = _ACTIONABLE_TASKS_RE.match(text)
        if summary:
            self.real_progress_seen = True
            self._synthetic_stop.set()
            self._merge_total(max(0, int(summary.group("total") or 0)))
            self.completed = self.total
            self._update_requested_dependency_counts()
            self.detail = project_live_output_line(text)
            self.fallback_lines += 1
            self._publish(force=True)
            return True

        heading = _HEADING_RE.match(text)
        if heading:
            heading_text = heading.group("heading")
            if heading_text.strip().lower() == "build":
                # The nested Android builder prints this immediately before it
                # launches Gradle. It is a launch boundary, not meaningful
                # Gradle progress; keeping synthetic startup alive prevents the
                # TUI from sitting on only "Build" during daemon/configuration.
                if self._process_started_at is None:
                    self._process_started_at = time.monotonic()
                self.detail = self._synthetic_detail_for_elapsed(0.0)
            else:
                self.real_progress_seen = True
                self._synthetic_stop.set()
                self.detail = heading_text
            self.fallback_lines += 1
            self._publish()
            return True

        if any(text.startswith(prefix) for prefix in _LIFECYCLE_PREFIXES):
            self.real_progress_seen = True
            self._synthetic_stop.set()
            self.detail = project_live_output_line(text)
            if text.startswith("BUILD FAILED"):
                self.errors = max(1, self.errors)
            self.fallback_lines += 1
            self._publish()
            return True

        return True

    def _update_tests(self, event: dict[str, Any]) -> None:
        self.tests_passed = int(event.get("passed") or 0)
        self.tests_failed = int(event.get("failed") or 0)
        self.tests_skipped = int(event.get("skipped") or 0)
        self.tests_total = int(event.get("total") or (self.tests_passed + self.tests_failed + self.tests_skipped))

    def finish(self, returncode: int) -> None:
        self._synthetic_stop.set()
        self.returncode = returncode
        if self.total is None and returncode == 0:
            inferred_total = max(int(self.completed or 0), len(self.observed_tasks))
            if inferred_total > 0:
                self.total = inferred_total
        if returncode == 0 and self.total is not None:
            self.completed = self.total
        self._update_requested_dependency_counts()
        if returncode == 0:
            status = "Gradle completed"
        elif self.failed_task:
            status = f"Gradle failed at {self.failed_task} rc={returncode}"
        else:
            status = f"Gradle failed rc={returncode}"
        if self.tests_total:
            status += (
                f" • tests: {self.tests_passed} passed, {self.tests_failed} failed, "
                f"{self.tests_skipped} skipped"
            )
        self.detail = status
        self._publish(force=True)
        self._print_completion_once()

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
            "failed_task": self.failed_task,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_skipped": self.tests_skipped,
            "tests_total": self.tests_total,
            "event_sequence": self.last_sequence,
            "rejected_events": self.rejected_events,
            "run_id": self.expected_run_id,
            "requested_tasks": list(self.requested_tasks),
            "requested_task_count": self.requested_task_count,
            "dependency_task_count": self.dependency_task_count,
            "requested_duplicate_count": self.requested_duplicate_count,
            "requested_duplicate_tasks": dict(self.requested_duplicate_tasks),
            "task_execution_count": len(self.started_tasks),
            "task_completed_count": len(self.completed_tasks),
            "task_resume_count": sum(self.task_resume_counts.values()),
            "task_resume_counts": dict(self.task_resume_counts),
            "task_header_count": sum(self.task_header_counts.values()),
            "task_header_counts": dict(self.task_header_counts),
            "task_outcomes": dict(self.task_outcomes),
            "duplicate_execution_start_count": self.duplicate_execution_start_count,
            "duplicate_terminal_header_count": self.duplicate_terminal_header_count,
            "structured_events_seen": self.structured_events_seen,
            "real_progress_seen": self.real_progress_seen,
            "startup_state": "real" if self.real_progress_seen else self.detail,
            "fallback_lines": self.fallback_lines,
            "observed_tasks": sorted(self.observed_tasks),
        }

    def task_telemetry_line(self) -> str:
        snapshot = self.snapshot()
        return (
            "Devtool Gradle task telemetry: "
            f"requested={len(self.requested_tasks)} unique"
            f" duplicates={snapshot['requested_duplicate_count']}; "
            f"executions={snapshot['task_execution_count']} unique; "
            f"completed={snapshot['task_completed_count']}; "
            f"resumed_headers={snapshot['task_resume_count']}; "
            f"duplicate_execution_starts={snapshot['duplicate_execution_start_count']}"
        )

    def has_task_telemetry(self) -> bool:
        return bool(
            self.requested_tasks
            or self.started_tasks
            or self.task_header_counts
            or self.structured_task_start_counts
        )

    def _publish(self, *, force: bool = False) -> None:
        if self.live is not None:
            self.live.update(self._renderable())
        if self.update_callback is not None:
            snapshot = self.snapshot()
            snapshot["force"] = force
            self.update_callback(snapshot)

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

    def _merge_total(self, value: Any) -> None:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return
        if candidate <= 0:
            return
        if self.total is None or candidate > self.total:
            self.total = candidate

    def _merge_completed(self, value: Any) -> None:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return
        if candidate < 0:
            return
        if self.completed is None or candidate > self.completed:
            self.completed = candidate

    @staticmethod
    def _canonical_task(task: str) -> str:
        value = str(task or "").strip()
        if not value:
            return ""
        return ":" + value.strip(":")

    def _task_is_requested(self, task: str) -> bool:
        current = self._canonical_task(task)
        current_leaf = current.rsplit(":", 1)[-1]
        for requested in self.requested_tasks:
            canonical = self._canonical_task(requested)
            if canonical == current:
                return True
            requested_body = canonical.strip(":")
            if ":" not in requested_body and requested_body == current_leaf:
                return True
        return False

    def _update_requested_dependency_counts(self) -> None:
        if not self.requested_tasks or self.total is None:
            self.requested_task_count = 0
            self.dependency_task_count = 0
            return
        if self.graph_tasks:
            requested = sum(1 for task in self.graph_tasks if self._task_is_requested(task))
        else:
            requested = min(len(self.requested_tasks), self.total)
        self.requested_task_count = requested
        self.dependency_task_count = max(0, self.total - requested)

    def _current_task_role(self) -> tuple[str, str]:
        if not self.current_task or not self.requested_tasks:
            return "", ""
        if self._task_is_requested(self.current_task):
            return "[requested]", "[requested]"
        return "[dependency]", "[dep]"

    def _progress_state(self) -> ThreeLineProgressState:
        extra_full = extra_compact = extra_tiny = ""
        if self.requested_tasks:
            if self.total is None and self.returncode is None:
                extra_full = "discovering dependencies"
                extra_compact = "finding deps"
                extra_tiny = "deps…"
            elif self.total is None:
                extra_full = f"{len(self.requested_tasks)} requested • graph total unavailable"
                extra_compact = f"{len(self.requested_tasks)} req • total unknown"
                extra_tiny = f"{len(self.requested_tasks)}r"
            else:
                extra_full = f"{self.requested_task_count} requested + {self.dependency_task_count} deps"
                extra_compact = f"{self.requested_task_count} req + {self.dependency_task_count} deps"
                extra_tiny = f"{self.requested_task_count}r+{self.dependency_task_count}d"
        prefix_full, prefix_compact = self._current_task_role()
        detail = self.current_task or self.detail or "Preparing Gradle environment"
        return ThreeLineProgressState(
            completed=int(self.completed or 0),
            total=self.total,
            unit_label="tasks",
            unit_short="tasks",
            detail=detail,
            detail_root=self.cwd,
            detail_prefix_full=prefix_full,
            detail_prefix_compact=prefix_compact,
            extra_full=extra_full,
            extra_compact=extra_compact,
            extra_tiny=extra_tiny,
            started_at=self._started_at,
            returncode=self.returncode,
            phase_index=self.phase_index,
            phase_total=self.phase_total,
            phase_label=self.command_name,
            tests_passed=self.tests_passed,
            tests_failed=self.tests_failed,
            tests_skipped=self.tests_skipped,
            tests_total=self.tests_total,
            warnings=self.warnings,
            errors=self.errors,
        )

    def _requested_tasks_renderable(self) -> Group:
        if self.console.width <= 96:
            first = self.requested_tasks[0] if self.requested_tasks else ""
            suffix = f" (+{len(self.requested_tasks) - 1})" if len(self.requested_tasks) > 1 else ""
            return Group(Text(f"Requested: {first}{suffix}", style="dim cyan", overflow="ellipsis", no_wrap=True))
        lines: list[Text] = [Text("Requested tasks:", style="bold")]
        lines.extend(Text(f"  {task}", style="cyan") for task in self.requested_tasks)
        return Group(*lines)

    def _renderable(self) -> Panel:
        target = compact_display_target(self.display_target or self.cwd or "Gradle project", cwd=self.cwd)
        if self.returncode is None:
            icon, border, style = activity_icon(kind="gradle", command=self.command_name or "Gradle", target=f"Gradle · {target}", cwd=self.cwd), "cyan", "bold cyan"
        elif self.returncode == 0:
            icon, border, style = "✓", "green", "bold green"
        else:
            icon, border, style = "✗", "red", "bold red"
        title = active_panel_title(
            self.command_name or "Gradle",
            f"Gradle · {target}",
            icon=icon,
            width=self.console.width,
        )
        title.stylize(style)
        body = self.progress_body or ThreeLineProgressBody(self._progress_state, inspection=self.inspection)
        return Panel(body, title=title, border_style=border, padding=(0, 1))
