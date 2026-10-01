"""Interactive execution presentation controller for normal Devtool work.

PRES-R01 moves terminal ownership out of progress parsers.  Gradle/.NET/npm/
generic trackers publish semantic snapshots; this module decides how those
snapshots are presented.  Interactive TTY sessions prefer a small Textual app.
Non-TTY, CI, verbose and machine-oriented sessions keep ordinary stream output.

The controller intentionally owns no execution truth.  Cancellation is injected
as a callback to the runner and all durable output remains owned by RUN/logging.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from queue import Empty, Queue
import sys
import threading
import time
from typing import Any, Callable, Mapping

from rich.console import Console

from .textual_ux import TEXTUAL_SHARED_CSS, fit_middle, progress_counter, progress_percent, responsive_class_names, status_label


_TRUE = {"1", "true", "yes", "on"}

@dataclass(frozen=True, slots=True)
class ExecutionPresentationDecision:
    backend: str
    interactive: bool
    hides_raw_output: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "interactive": self.interactive,
            "hides_raw_output": self.hides_raw_output,
            "reason": self.reason,
        }


def _isatty(stream: object) -> bool:
    try:
        return bool(getattr(stream, "isatty")())
    except (AttributeError, OSError, ValueError, TypeError):
        return False


def textual_available() -> bool:
    """Probe Textual lazily so plain/machine paths never import it."""

    try:
        return importlib.util.find_spec("textual") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def select_execution_presentation(
    console: Console,
    *,
    verbose: bool = False,
    external_surface: bool = False,
    env: Mapping[str, str] | None = None,
    stdin: object | None = None,
) -> ExecutionPresentationDecision:
    """Choose Textual interactive presentation or plain streaming.

    ``DEVTOOL_EXECUTION_PRESENTATION`` may be ``auto`` (default), ``textual`` or
    ``plain``.  An explicit ``textual`` request still fails safe to plain when
    Textual or a real TTY is unavailable; execution must never fail because a
    presentation dependency is missing.
    """

    environment = env if env is not None else os.environ
    requested = str(environment.get("DEVTOOL_EXECUTION_PRESENTATION", "auto") or "auto").strip().lower()
    if requested not in {"auto", "textual", "plain"}:
        requested = "auto"
    if verbose:
        return ExecutionPresentationDecision("plain", False, False, "verbose output requested")
    if external_surface:
        return ExecutionPresentationDecision("plain", False, False, "external Tasker/HUD surface owns presentation")
    if requested == "plain":
        return ExecutionPresentationDecision("plain", False, False, "plain execution presentation requested")
    if str(environment.get("CI", "")).strip().lower() in _TRUE:
        return ExecutionPresentationDecision("plain", False, False, "CI environment")
    input_stream = sys.stdin if stdin is None else stdin
    if not bool(console.is_terminal or console.is_interactive) or not _isatty(input_stream):
        return ExecutionPresentationDecision("plain", False, False, "non-interactive terminal")
    if not textual_available():
        return ExecutionPresentationDecision("plain", False, False, "Textual package unavailable")
    return ExecutionPresentationDecision("textual", True, True, "interactive TTY with Textual available")


class _PlainExecutionPresenter:
    interactive = False
    hides_raw_output = False

    def start(self, snapshot: Mapping[str, Any] | None = None) -> None:
        return None

    def update(self, snapshot: Mapping[str, Any]) -> None:
        return None

    def output_line(self, line: str) -> None:
        return None

    def set_stop_callback(self, callback: Callable[[], None] | None) -> None:
        return None

    def stop(self) -> None:
        return None



def build_textual_execution_app(source: Any) -> Any:
    """Build the concrete normal-execution Textual app.

    The app is intentionally a projection over the queue-backed source.  The
    revamp keeps the long-running command, current stage, progress and raw output
    visible in one responsive workspace while leaving execution truth in RUN/EXO.
    """

    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Footer, Header, ProgressBar, RichLog, Static

    class ExecutionProgressApp(App):
        TITLE = "Devtool"
        SUB_TITLE = "Live execution"
        BINDINGS = [
            ("ctrl+c", "request_cancel", "Cancel"),
            ("l", "toggle_logs", "Logs"),
        ]
        CSS = TEXTUAL_SHARED_CSS + """
        #execution-root { height: 1fr; min-height: 0; }
        #execution-context { height: auto; min-height: 3; }
        #execution-status { height: 1; text-style: bold; overflow-x: hidden; overflow-y: hidden; }
        #execution-meta { height: 1; color: $text-muted; overflow-x: hidden; overflow-y: hidden; }
        #execution-progress-row { height: 1; min-height: 1; padding: 0 1; }
        #execution-progress { width: 1fr; height: 1; margin-right: 1; }
        #execution-progress-meta { width: auto; min-width: 8; height: 1; content-align: right middle; color: $text-muted; }
        #execution-detail { height: auto; min-height: 1; max-height: 2; padding: 0 1; overflow-x: hidden; overflow-y: hidden; }
        #execution-log-toolbar { height: 3; min-height: 3; padding: 0 1; border-top: solid $panel; }
        #execution-log-title { width: 1fr; height: 1; content-align: left middle; text-style: bold; overflow-x: hidden; overflow-y: hidden; }
        #execution-log-toggle { width: auto; min-width: 10; }
        #execution-log { height: 1fr; min-height: 1; border-top: solid $surface-lighten-2; }
        #execution-cancel { height: auto; min-height: 0; max-height: 1; padding: 0 1; color: $warning; overflow-x: hidden; overflow-y: hidden; }
        .compact #execution-progress-row,
        .compact #execution-detail,
        .compact #execution-log-toolbar,
        .compact #execution-cancel { padding-left: 0; padding-right: 0; }
        .compact #execution-progress-meta { min-width: 6; }
        .short #execution-context { min-height: 2; }
        .short #execution-detail { max-height: 1; }
        .short #execution-log-toolbar { height: 1; min-height: 1; }
        .short #execution-log-toggle { display: none; }
        .tiny #execution-meta { display: none; }
        """

        def __init__(self) -> None:
            super().__init__()
            self._cancel_armed_until = 0.0
            self._logs_visible = True
            self._last_snapshot: dict[str, Any] = {}

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical(id="execution-root"):
                with Vertical(id="execution-context", classes="ux-context"):
                    yield Static("DEVTOOL EXECUTION", classes="ux-kicker")
                    yield Static("Starting…", id="execution-status", classes="ux-title")
                    yield Static("Canonical RUN/EXO progress", id="execution-meta")
                with Horizontal(id="execution-progress-row"):
                    yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="execution-progress")
                    yield Static("—", id="execution-progress-meta")
                yield Static("Preparing command", id="execution-detail")
                yield Static("", id="execution-cancel")
                with Horizontal(id="execution-log-toolbar"):
                    yield Static("Live output", id="execution-log-title")
                    yield Button("Hide logs", id="execution-log-toggle")
                yield RichLog(id="execution-log", wrap=True, highlight=False, markup=False, max_lines=source.max_log_lines)
            yield Footer()

        def _apply_responsive_geometry(self, width: int, height: int) -> None:
            desired = set(responsive_class_names(width, height))
            for name in ("compact", "ultra-compact", "short", "tiny"):
                if name in desired:
                    self.screen.add_class(name)
                else:
                    self.screen.remove_class(name)
            try:
                self.screen.refresh(layout=True)
            except Exception:
                pass

        def on_mount(self) -> None:
            self._apply_responsive_geometry(self.size.width, self.size.height)
            source._ready.set()
            self.set_interval(1 / 12, self._drain_updates)

        def on_resize(self, event: Any) -> None:
            self._apply_responsive_geometry(event.size.width, event.size.height)

        def _drain_updates(self) -> None:
            for _ in range(128):
                try:
                    kind, payload = source._queue.get_nowait()
                except Empty:
                    break
                if kind == "stop":
                    self.exit()
                    return
                if kind == "line":
                    if self._logs_visible:
                        self.query_one("#execution-log", RichLog).write(str(payload))
                    continue
                if kind == "snapshot" and isinstance(payload, Mapping):
                    self._apply_snapshot(payload)

        def _apply_snapshot(self, snapshot: Mapping[str, Any]) -> None:
            self._last_snapshot = dict(snapshot)
            command = str(snapshot.get("command") or "Command")
            phase = str(snapshot.get("phase_name") or "")
            target = str(snapshot.get("target") or "")
            title = command if not phase or phase == command else f"{command} · {phase}"
            if target:
                title += f" · {target}"
            self.query_one("#execution-status", Static).update(fit_middle(title, max(24, self.size.width - 2)))
            completion = status_label(snapshot.get("completion_status") or "running")
            meta = f"{completion} · canonical RUN/EXO projection"
            self.query_one("#execution-meta", Static).update(meta)
            detail = str(snapshot.get("detail") or snapshot.get("completion_status") or "Working…")
            self.query_one("#execution-detail", Static).update(fit_middle(detail, max(24, self.size.width - 2)))
            percent = progress_percent(snapshot)
            counter = progress_counter(snapshot)
            progress = self.query_one("#execution-progress", ProgressBar)
            progress.display = percent is not None
            if percent is not None:
                progress.update(progress=percent)
            self.query_one("#execution-progress-meta", Static).update(counter or "working")

        def action_toggle_logs(self) -> None:
            log = self.query_one("#execution-log", RichLog)
            self._logs_visible = not self._logs_visible
            log.display = self._logs_visible
            self.query_one("#execution-log-toggle", Button).label = "Hide logs" if self._logs_visible else "Show logs"

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if (event.button.id or "") == "execution-log-toggle":
                self.action_toggle_logs()

        def action_request_cancel(self) -> None:
            now = time.monotonic()
            notice = self.query_one("#execution-cancel", Static)
            if now <= self._cancel_armed_until:
                self._cancel_armed_until = 0.0
                notice.update("Cancellation requested · waiting for canonical recovery/finalization")
                source._request_cancel()
                return
            self._cancel_armed_until = now + 3.0
            notice.update("Press Ctrl+C again within 3s to cancel the current command")

    return ExecutionProgressApp()

class _TextualExecutionPresenter:
    """Queue-backed source for the normal-execution Textual application.

    Textual's Linux terminal driver installs process signal handlers and therefore
    must run on the main interpreter thread.  PRES-H01 deliberately forbids this
    synchronous presenter from spawning ``App.run()`` in a helper thread.  The
    concrete app remains available to ``App.run_test`` and to the forthcoming
    main-thread execution host; until that host owns the terminal event loop, the
    controller falls back to plain streaming rather than risking a terminal-driver
    traceback or terminal corruption.
    """

    interactive = True
    hides_raw_output = True
    terminal_host_ready = False

    def __init__(self, *, max_log_lines: int = 400) -> None:
        self.max_log_lines = max(50, int(max_log_lines))
        self._queue: Queue[tuple[str, object]] = Queue(maxsize=max(256, self.max_log_lines * 2))
        self._ready = threading.Event()
        self._stop_callback: Callable[[], None] | None = None
        self._lock = threading.RLock()

    def _offer(self, kind: str, payload: object) -> None:
        try:
            self._queue.put_nowait((kind, payload))
        except Exception:
            # Presentation backpressure must not block execution. Prefer dropping
            # an intermediate update over perturbing canonical runner truth.
            try:
                self._queue.get_nowait()
            except Empty:
                pass
            try:
                self._queue.put_nowait((kind, payload))
            except Exception:
                pass

    def start(self, snapshot: Mapping[str, Any] | None = None) -> None:
        if snapshot:
            self._offer("snapshot", dict(snapshot))
        raise RuntimeError(
            "Textual terminal execution requires the main-thread presentation host; "
            "background App.run() is intentionally disabled"
        )

    def update(self, snapshot: Mapping[str, Any]) -> None:
        self._offer("snapshot", dict(snapshot))

    def output_line(self, line: str) -> None:
        clean = str(line or "").rstrip("\r\n")
        if clean:
            self._offer("line", clean)

    def set_stop_callback(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            self._stop_callback = callback

    def _request_cancel(self) -> None:
        with self._lock:
            callback = self._stop_callback
        if callback is not None:
            callback()

    def stop(self) -> None:
        return None


def create_textual_execution_source(*, max_log_lines: int = 400) -> Any:
    """Return the queue-backed source used by the main-thread execution host.

    The source owns presentation queues only. Canonical execution remains in the
    managed child process and Tasker/RUN stores.
    """

    return _TextualExecutionPresenter(max_log_lines=max_log_lines)


class ExecutionPresentationController:
    """Own the selected normal-execution presentation backend."""

    def __init__(
        self,
        console: Console,
        *,
        verbose: bool = False,
        external_surface: bool = False,
        env: Mapping[str, str] | None = None,
        stdin: object | None = None,
        presenter_factory: Callable[[ExecutionPresentationDecision], object] | None = None,
    ) -> None:
        self.decision = select_execution_presentation(
            console,
            verbose=verbose,
            external_surface=external_surface,
            env=env,
            stdin=stdin,
        )
        if presenter_factory is not None:
            self._presenter = presenter_factory(self.decision)
        elif self.decision.backend == "textual":
            self._presenter = _TextualExecutionPresenter()
        else:
            self._presenter = _PlainExecutionPresenter()
        self._started = False

    @property
    def interactive(self) -> bool:
        return bool(self.decision.interactive and getattr(self._presenter, "interactive", False))

    @property
    def hides_raw_output(self) -> bool:
        return bool(self.interactive and self.decision.hides_raw_output and getattr(self._presenter, "hides_raw_output", False))

    def start(self, snapshot: Mapping[str, Any] | None = None) -> None:
        if self._started:
            if snapshot:
                self.update(snapshot)
            return
        if not bool(getattr(self._presenter, "terminal_host_ready", True)):
            self._presenter = _PlainExecutionPresenter()
            self.decision = ExecutionPresentationDecision(
                "plain",
                False,
                False,
                "Textual terminal driver requires the main-thread presentation host; fell back to plain",
            )
        try:
            self._presenter.start(snapshot)
            self._started = True
        except Exception:
            # Presentation is secondary to execution. Fail safely to plain output.
            self._presenter = _PlainExecutionPresenter()
            self.decision = ExecutionPresentationDecision("plain", False, False, "Textual presenter startup failed; fell back to plain")
            self._presenter.start(snapshot)
            self._started = True

    def update(self, snapshot: Mapping[str, Any]) -> None:
        try:
            self._presenter.update(snapshot)
        except Exception:
            pass

    def output_line(self, line: str) -> None:
        try:
            self._presenter.output_line(line)
        except Exception:
            pass

    def set_stop_callback(self, callback: Callable[[], None] | None) -> None:
        try:
            self._presenter.set_stop_callback(callback)
        except Exception:
            pass

    def stop(self) -> None:
        if not self._started:
            return
        try:
            self._presenter.stop()
        finally:
            self._started = False

    def __enter__(self) -> "ExecutionPresentationController":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()
