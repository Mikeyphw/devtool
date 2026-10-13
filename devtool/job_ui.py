"""Compact, Termux-friendly Textual presentation of canonical Devtool jobs.

UI-only state lives here.  The managed child and LIFE/RUN still own exit status,
logs, mutation, rollback and cancellation; a renderer cannot qualify a job.
"""
from __future__ import annotations

import os
import re
import time
from collections import deque
from queue import Empty
from typing import Any, Mapping

from .textual_ux import fit_middle, normalized_status, progress_counter, progress_percent, status_label

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SGR = re.compile(r"\x1b\[[0-9;]*m")
_DOT_LINE = re.compile(r"^[.sFxX-]{8,}(?:\s+\[\s*\d+%\])?\s*$")
_ACTIVITY = re.compile(
    r"(?:\b(?:error|failed|failure|warning|traceback|exception|fatal|cancelled|" 
    r"rollback|recover|validat|commit|compile|complete|success|passed|" 
    r"started|finished|progress|applied|artifact|download|upload|" 
    r"task|job|step|github|run id|exit code|transaction)\b|^\$\s|^>\s*Task|^---\s*devtool)",
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
    """Use a clean alternate screen for both compact and full execution hosts."""
    kwargs: dict[str, Any] = {}
    # Compact has a clean alternate-screen canvas just like the full host.
    # Inline mode left stale shell output / giant blank regions on Termux and
    # clipped the live log to a tiny strip at the bottom of the viewport.
    # Textual restores the terminal's original screen when the app exits.
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


def safe_ansi_line(value: object, *, max_chars: int = 2048) -> str:
    """Keep only safe color SGR codes. No cursor actions, OSC, or controls.

    The result can be passed to Rich's ANSI parser. It is bounded independent
    of how many control sequences an untrusted subprocess emits.
    """
    raw = str(value or "")[:max_chars * 4]
    chunks: list[str] = []
    offset = 0
    for token in _SGR.finditer(raw):
        chunks.append(_ANSI.sub("", raw[offset:token.start()]))
        chunks.append(token.group())
        offset = token.end()
    chunks.append(_ANSI.sub("", raw[offset:]))
    safe = "".join(chunks).replace("\r", " ").rstrip("\n")
    # Strip unmatched ESC bytes (including incomplete CSI/OSC) and all C0
    # controls except tab. Even raw output from failed commands stays inert.
    safe = "".join(c for c in safe if c == "\t" or (c.isprintable() and c != "\x7f") or c == "\x1b")
    safe = re.sub(r"\x1b(?!\[[0-9;]*m)", "", safe)
    if len(readable_job_line(safe, max_chars=max_chars * 4)) > max_chars:
        # Avoid truncating in the middle of a color control sequence.
        safe = readable_job_line(raw, max_chars=max_chars)
    return safe[: max_chars * 2]


def styled_job_line(value: object, *, no_color: bool | None = None):
    """Preserve Rich/ANSI color and emphasis without terminal side effects.

    RichLog expects a Rich Text object; writing a literal string would show
    escape codes as text. Log files remain the unmodified source of truth.
    """
    from rich.text import Text

    if no_color is None:
        no_color = "NO_COLOR" in os.environ
    plain = readable_job_line(value)
    if no_color:
        return Text(plain)
    colored = safe_ansi_line(value)
    result = Text.from_ansi(colored)
    if not _SGR.search(colored):
        low = plain.lower()
        if any(token in low for token in ("failed", "error:", "traceback", "fatal")):
            result.stylize("bold red")
        elif any(token in low for token in ("warning", "skipped")):
            result.stylize("yellow")
        elif any(token in low for token in ("passed", "success", "completed")):
            result.stylize("green")
    return result


def job_icons(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Nerd Font is opt-in: Termux's standard font must never display tofu."""
    values = os.environ if env is None else env
    enabled = str(values.get("DEVTOOL_NERD_FONT", "") or "").lower() in {"1", "on", "true", "yes"}
    if enabled:
        return {"brand": "\uf489", "done": "\uf00c", "active": "\uf110", "pending": "\uf10c", "failed": "\uf00d", "logs": "\uf120"}
    return {"brand": "◆", "done": "✓", "active": "●", "pending": "○", "failed": "✕", "logs": "▤"}


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
        # Retain safe SGR in the bounded in-memory view. Filtering decisions
        # use plain text; original on-disk output is untouched.
        colored = safe_ansi_line(text)
        if not colored or not readable_job_line(colored).strip():
            return "", False
        self.raw.append(colored)
        visible = activity_job_line(colored)
        if visible:
            self.activity.append(colored)
        return colored, visible


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
        "counter": (status_label(raw_status).upper() if normalized in {"completed", "failed", "cancelled"}
                    else progress_counter(state) or "working"),
        "finished": normalized in {"completed", "failed", "cancelled"},
    }


def build_compact_execution_app(source: Any, *, run_id: str = "", remote: bool = False) -> Any:
    """Main-thread full-screen Textual app consuming canonical progress events."""
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import ProgressBar, RichLog, Static

    icons = job_icons()

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
        #job-root { height: 1fr; min-height: 0; padding: 1 2; }
        #job-header { height: 3; border-bottom: solid $accent; margin-bottom: 1; }
        #job-brand { height: 1; text-style: bold; color: $accent; }
        #job-title { height: 1; text-style: bold; overflow-x: hidden; }
        #job-meta { height: 1; color: $text-muted; overflow-x: hidden; }
        #job-section { height: 1; color: $text-muted; text-style: bold; }
        #job-row { height: 1; min-height: 1; }
        #job-progress { width: 1fr; height: 1; margin-right: 1; }
        #job-count { width: auto; min-width: 7; height: 1; content-align: right middle; }
        #job-detail { height: 2; margin-top: 1; overflow-x: hidden; }
        #job-stages { height: 5; min-height: 0; color: $text; overflow-x: hidden; }
        #job-log-label { height: 1; color: $accent; text-style: bold; }
        #job-logs { height: 1fr; min-height: 2; border: round $panel; }
        #job-help { height: 1; margin-top: 1; color: $text-muted; }
        .narrow #job-root { padding: 0 1; }
        .narrow #job-header { height: 2; }
        .narrow #job-count { min-width: 5; }
        .short #job-root { padding-top: 0; padding-bottom: 0; }
        .short #job-detail { height: 1; margin-top: 0; }
        .short #job-stages { height: 2; }
        .short #job-header { margin-bottom: 0; }
        .tiny #job-brand, .tiny #job-section, .tiny #job-stages { display: none; }
        .tiny #job-header { height: 2; }
        .tiny #job-help { margin-top: 0; }
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
            self._phase_history: deque[str] = deque(maxlen=4)
            self._poll_timer: Any | None = None

        def compose(self) -> ComposeResult:
            with Vertical(id="job-root"):
                with Vertical(id="job-header"):
                    yield Static(f"{icons['brand']}  DEVTOOL  /  LIVE EXECUTION", id="job-brand")
                    yield Static("Starting job…", id="job-title")
                    yield Static("Preparing managed command", id="job-meta")
                yield Static("PROGRESS  ·  verified runtime status", id="job-section")
                with Horizontal(id="job-row"):
                    yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="job-progress")
                    yield Static("working", id="job-count")
                yield Static("Waiting for canonical progress", id="job-detail")
                yield Static("Awaiting observed stages", id="job-stages")
                yield Static(f"{icons['logs']}  Activity · r raw", id="job-log-label")
                yield RichLog(id="job-logs", wrap=True, highlight=False, markup=False, max_lines=self._buffer.raw.maxlen)
                yield Static("l logs · r raw · Ctrl+C ×2 " + ("detach" if remote else "cancel") + " · q close", id="job-help")

        def _geometry(self, width: int, height: int) -> None:
            screen = self.screen
            screen.set_class(width < 52, "narrow")
            screen.set_class(height < 24, "short")
            screen.set_class(height < 16, "tiny")
            # Fill the alternate screen but keep controls reachable on phones.
            self.query_one("#job-logs", RichLog).display = self._logs_visible
            if width < 36:
                self.query_one("#job-help", Static).update("l logs · r raw · ^C×2 " + ("detach" if remote else "cancel"))
            elif not self._finished:
                self.query_one("#job-help", Static).update("l logs · r raw · Ctrl+C ×2 " + ("detach" if remote else "cancel") + " · q close")

        def on_mount(self) -> None:
            source._ready.set()
            self._geometry(self.size.width, self.size.height)
            self._poll_timer = self.set_interval(1 / 12, self._drain)

        def on_resize(self, event: Any) -> None:
            self._geometry(event.size.width, event.size.height)

        def _render_meta(self) -> None:
            from textual.css.query import NoMatches
            elapsed = int(time.monotonic() - self._start)
            ref = f" · {run_id[:8]}" if run_id else ""
            try:
                self.query_one("#job-meta", Static).update(
                    fit_middle(f"{self._last_status} · {elapsed}s{ref}", max(8, self.size.width - 2))
                )
            except NoMatches:
                # An in-flight timer can fire during the last render teardown.
                # Nothing may resurrect or affect a finished/closing job.
                return

        def _log_write(self, line: str, *, activity: bool) -> None:
            if self._logs_visible and (self._raw_mode or activity):
                self.query_one("#job-logs", RichLog).write(styled_job_line(line))

        def _replay(self) -> None:
            log = self.query_one("#job-logs", RichLog)
            log.clear()
            if self._logs_visible:
                for line in (self._buffer.raw if self._raw_mode else self._buffer.activity):
                    log.write(styled_job_line(line))
            self.query_one("#job-log-label", Static).update(
                f"{icons['logs']}  " + ("Raw" if self._raw_mode else "Activity") + f" · {len(self._buffer.raw)} recent lines · r toggle"
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
            # Textual supports a real indeterminate bar (total=None). Never
            # replace unknown Gradle progress with a synthetic percentage.
            if view["percent"] is None:
                widget.update(total=None)
                widget.display = not view["finished"]
            else:
                widget.update(total=100, progress=view["percent"])
                widget.display = True
            self.query_one("#job-count", Static).update(view["counter"])
            stages = state.get("stages")
            if isinstance(stages, list):
                rows = []
                for stage in stages[:5]:
                    if not isinstance(stage, Mapping):
                        continue
                    name = readable_job_line(stage.get("name"), max_chars=120)
                    state_name = str(stage.get("status") or "unknown").lower()
                    glyph = (icons["failed"] if state_name in {"failure", "failed", "cancelled", "timed_out"}
                             else icons["done"] if state_name in {"success", "completed", "skipped"}
                             else icons["active"] if state_name in {"running", "in_progress"}
                             else icons["pending"])
                    rows.append(f"{glyph} {name} · {state_name}")
                if rows:
                    self.query_one("#job-stages", Static).update("\n".join(rows))
            else:
                # Generic jobs expose canonical phase transitions rather than
                # individual verified GitHub steps. A previously SEEN phase is
                # not automatically marked successful.
                phase = readable_job_line(state.get("phase_name"), max_chars=120).strip()
                if phase and (not self._phase_history or self._phase_history[-1] != phase):
                    self._phase_history.append(phase)
                if self._phase_history:
                    rows = [f"{icons['pending']} {name} · observed" for name in list(self._phase_history)[:-1]]
                    status_icon = icons["done"] if view["normalized_status"] == "completed" else (icons["failed"] if view["finished"] else icons["active"])
                    rows.append(f"{status_icon} {self._phase_history[-1]} · {view['status']}")
                    self.query_one("#job-stages", Static).update("\n".join(rows[-5:]))
            if view["finished"]:
                self._finished = True
                self._outcome = view["normalized_status"]
                self.screen.add_class("finished" if self._outcome == "completed" else self._outcome)
                symbol = icons["done"] if self._outcome == "completed" else icons["failed"]
                self.query_one("#job-title", Static).update(f"{symbol}  {view['title']}")
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
                self.query_one("#job-help", Static).update("Job still running · Ctrl+C twice to " + ("detach" if remote else "cancel"))

        def action_request_cancel(self) -> None:
            if self._finished:
                return
            now = time.monotonic()
            if now <= self._armed_until:
                self._armed_until = 0.0
                source._request_cancel()
                self.query_one("#job-help", Static).update(
                    "Detaching locally; GitHub workflow continues" if remote else
                    "Cancellation requested · awaiting canonical finalization")
            else:
                self._armed_until = now + 3.0
                self.query_one("#job-help", Static).update("Press Ctrl+C again within 3s to " + ("detach" if remote else "cancel"))

        def on_unmount(self) -> None:
            if self._poll_timer is not None:
                self._poll_timer.stop()

    return CompactExecutionApp()
