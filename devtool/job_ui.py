"""Compact, Termux-friendly Textual presentation of canonical Devtool jobs.

UI-only state lives here.  The managed child and LIFE/RUN still own exit status,
logs, mutation, rollback and cancellation; a renderer cannot qualify a job.
"""
from __future__ import annotations

import inspect
import os
import re
import time
from collections import deque
from queue import Empty
from typing import Any, Mapping

from .textual_ux import fit_middle, normalized_status, progress_counter, progress_percent, status_label

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_DOT_LINE = re.compile(r"^[.sFxX-]{8,}(?:\s+\[\s*\d+%\])?\s*$")
_ACTIVITY = re.compile(
    r"(?:\b(?:error|failed|failure|warning|traceback|exception|fatal|cancelled|" 
    r"rollback|recover|validat|commit|compile|complete|success|passed|" 
    r"started|finished|progress|applied|artifact|download|upload|" 
    r"task|run id|exit code|transaction)\b|^\$\s|^>\s*Task|^---\s*devtool)",
    re.IGNORECASE,
)


def job_ui_mode(env: Mapping[str, str] | None = None) -> str:
    """Compact by default; no Rich fallback or dependency on Android features."""
    values = os.environ if env is None else env
    raw = str(values.get("DEVTOOL_JOB_UI", "compact") or "compact").strip().lower()
    if raw in {"plain", "off", "disabled"}:
        return "plain"
    if raw in {"full", "textual"}:
        return "full"
    return "compact"


def run_job_app(app: Any, *, mode: str) -> Any:
    """Ask Textual for inline/no-clear only if this installed version supports it."""
    kwargs: dict[str, Any] = {}
    if mode == "compact":
        params = inspect.signature(app.run).parameters
        if "inline" in params:
            kwargs["inline"] = True
        if "inline_no_clear" in params:
            kwargs["inline_no_clear"] = True
    return app.run(**kwargs)


def readable_job_line(value: object, *, max_chars: int = 2048) -> str:
    """Sanitize terminal control sequences and bound lines shown in the UI.

    Do not rewrite the original logs on disk. Truncation only affects display.
    """
    text = _ANSI.sub("", str(value or "")).replace("\r", " ").rstrip("\n")
    text = "".join(ch if ch == "\t" or (ch.isprintable() and ch != "\x7f") else " " for ch in text)
    if len(text) > max_chars:
        text = text[:max_chars - 1] + "…"
    return text


def activity_job_line(value: object) -> bool:
    """Show meaningful activity, never interpreting hidden lines as success."""
    text = readable_job_line(value)
    if not text.strip() or _DOT_LINE.fullmatch(text.strip()):
        return False
    return bool(_ACTIVITY.search(text))


class CompactLogBuffer:
    """Bounded buffers for raw/activity views; never retain unbounded build logs."""

    def __init__(self, max_lines: int = 240) -> None:
        self.raw: deque[str] = deque(maxlen=max(20, min(240, int(max_lines))))
        self.activity: deque[str] = deque(maxlen=self.raw.maxlen)

    def append(self, text: object) -> tuple[str, bool]:
        clean = readable_job_line(text)
        if not clean:
            return "", False
        self.raw.append(clean)
        visible = activity_job_line(clean)
        if visible:
            self.activity.append(clean)
        return clean, visible


def compact_job_projection(state: Mapping[str, Any], *, width: int = 80) -> dict[str, Any]:
    """Side-effect-free snapshot; unknown progress remains indeterminate."""
    command = str(state.get("command") or "Job")
    phase = str(state.get("phase_name") or "")
    target = str(state.get("target") or "")
    raw_status = str(state.get("completion_status") or "running")
    normalized = normalized_status(raw_status)
    length = max(8, width - 2)
    return {
        "title": fit_middle(" · ".join(part for part in (command, phase, target) if part), length),
        "status": status_label(raw_status),
        "normalized_status": normalized,
        "detail": fit_middle(str(state.get("detail") or "Working…"), length),
        "percent": progress_percent(state),
        "counter": progress_counter(state) or "working",
        "finished": normalized in {"completed", "failed", "cancelled"},
    }


def build_compact_execution_app(source: Any, *, run_id: str = "") -> Any:
    """Main-thread inline Textual app consuming existing canonical progress events."""
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import ProgressBar, RichLog, Static

    class CompactExecutionApp(App):
        TITLE = "Devtool job"
        BINDINGS = [
            ("l", "toggle_logs", "Logs"),
            ("r", "toggle_raw", "Raw/activity"),
            ("ctrl+c", "request_cancel", "Cancel"),
            ("q", "close_finished", "Close"),
            ("enter", "close_finished", "Close"),
        ]
        CSS = """
        Screen { background: $background; }
        #job-root { height: 1fr; min-height: 0; padding: 0 1; }
        #job-title { height: 1; text-style: bold; color: $text; overflow-x: hidden; }
        #job-meta { height: 1; color: $text-muted; overflow-x: hidden; }
        #job-row { height: 1; min-height: 1; }
        #job-progress { width: 1fr; height: 1; margin-right: 1; }
        #job-count { width: auto; min-width: 7; height: 1; content-align: right middle; }
        #job-detail { height: 1; overflow-x: hidden; }
        #job-log-label { height: 1; color: $text-muted; }
        #job-logs { height: 1fr; min-height: 1; max-height: 12; border-top: solid $surface-lighten-2; }
        #job-help { height: 1; color: $text-muted; overflow-x: hidden; }
        .narrow #job-root { padding: 0; }
        .narrow #job-count { min-width: 5; }
        .short #job-logs { max-height: 5; }
        .tiny #job-logs { max-height: 2; }
        .tiny #job-log-label { display: none; }
        .finished #job-title { color: $success; }
        .failed #job-title { color: $error; }
        .cancelled #job-title { color: $warning; }
        """

        def __init__(self) -> None:
            super().__init__()
            self._logs_visible = True
            self._raw_mode = False
            self._buffer = CompactLogBuffer(source.max_log_lines)
            self._finished = False
            self._outcome = ""
            self._armed_until = 0.0
            self._start = time.monotonic()
            self._last_status = "STARTING"
            self._last_meta = ""
            self._poll_timer: Any | None = None

        def compose(self) -> ComposeResult:
            with Vertical(id="job-root"):
                yield Static("Devtool · starting…", id="job-title")
                yield Static("Preparing managed command", id="job-meta")
                with Horizontal(id="job-row"):
                    yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="job-progress")
                    yield Static("working", id="job-count")
                yield Static("Waiting for canonical progress", id="job-detail")
                yield Static("Activity · r raw", id="job-log-label")
                yield RichLog(id="job-logs", wrap=True, highlight=False, markup=False, max_lines=self._buffer.raw.maxlen)
                yield Static("l logs · r raw · Ctrl+C ×2 cancel · q close", id="job-help")

        def _geometry(self, width: int, height: int) -> None:
            screen = self.screen
            screen.set_class(width < 52, "narrow")
            screen.set_class(height < 24, "short")
            screen.set_class(height < 16, "tiny")
            # The display is an inline app. Keep the log widget bounded on small phones.
            self.query_one("#job-logs", RichLog).display = self._logs_visible
            if width < 36:
                self.query_one("#job-help", Static).update("l logs · r raw · ^C×2 · q end")
            elif not self._finished:
                self.query_one("#job-help", Static).update("l logs · r raw · Ctrl+C ×2 cancel · q close")

        def on_mount(self) -> None:
            source._ready.set()
            self._geometry(self.size.width, self.size.height)
            self._poll_timer = self.set_interval(1 / 12, self._drain)

        def on_resize(self, event: Any) -> None:
            self._geometry(event.size.width, event.size.height)

        def _render_meta(self) -> None:
            elapsed = int(time.monotonic() - self._start)
            ref = f" · {run_id[:8]}" if run_id else ""
            self.query_one("#job-meta", Static).update(
                fit_middle(f"{self._last_status} · {elapsed}s{ref}", max(8, self.size.width - 2))
            )

        def _log_write(self, line: str, *, activity: bool) -> None:
            if self._logs_visible and (self._raw_mode or activity):
                self.query_one("#job-logs", RichLog).write(line)

        def _replay(self) -> None:
            log = self.query_one("#job-logs", RichLog)
            log.clear()
            if self._logs_visible:
                for line in (self._buffer.raw if self._raw_mode else self._buffer.activity):
                    log.write(line)
            self.query_one("#job-log-label", Static).update(
                ("Raw" if self._raw_mode else "Activity") + f" · {len(self._buffer.raw)} recent lines · r toggle"
            )

        def _drain(self) -> None:
            for _ in range(128):
                try:
                    kind, payload = source._queue.get_nowait()
                except Empty:
                    break
                if kind == "stop":
                    # A stopped presentation is not proof the job succeeded.
                    if not self._finished:
                        self._last_status = "UNKNOWN"
                        self.query_one("#job-detail", Static).update("Progress source stopped; verify the command exit status")
                    continue
                if kind == "line":
                    line, activity = self._buffer.append(payload)
                    if line:
                        self._log_write(line, activity=activity)
                elif kind == "snapshot" and isinstance(payload, Mapping):
                    self._snapshot(payload)
            self._render_meta()

        def _snapshot(self, state: Mapping[str, Any]) -> None:
            view = compact_job_projection(state, width=self.size.width)
            # Canonical final snapshots are terminal; stale intermediate snapshots
            # must never change the final result or re-enable cancellation.
            if self._finished:
                return
            self.query_one("#job-title", Static).update(view["title"])
            self._last_status = view["status"]
            self.query_one("#job-detail", Static).update(view["detail"])
            widget = self.query_one("#job-progress", ProgressBar)
            widget.display = view["percent"] is not None
            if view["percent"] is not None:
                widget.update(progress=view["percent"])
            self.query_one("#job-count", Static).update(view["counter"])
            if view["finished"]:
                self._finished = True
                self._outcome = view["normalized_status"]
                self.screen.add_class("finished" if self._outcome == "completed" else self._outcome)
                self.query_one("#job-help", Static).update(
                    f"{self._outcome.upper()} · r raw · l logs · q/Enter close"
                )

        def action_toggle_raw(self) -> None:
            self._raw_mode = not self._raw_mode
            self._replay()

        def action_toggle_logs(self) -> None:
            self._logs_visible = not self._logs_visible
            self.query_one("#job-logs", RichLog).display = self._logs_visible
            self._replay()

        def action_close_finished(self) -> None:
            if self._finished:
                self.exit()
            else:
                self.query_one("#job-help", Static).update("Job still running · Ctrl+C twice to cancel")

        def action_request_cancel(self) -> None:
            if self._finished:
                return
            now = time.monotonic()
            if now <= self._armed_until:
                self._armed_until = 0.0
                source._request_cancel()
                self.query_one("#job-help", Static).update("Cancellation requested · awaiting canonical finalization")
            else:
                self._armed_until = now + 3.0
                self.query_one("#job-help", Static).update("Press Ctrl+C again within 3s to cancel")

        def on_unmount(self) -> None:
            if self._poll_timer is not None:
                self._poll_timer.stop()

    return CompactExecutionApp()
