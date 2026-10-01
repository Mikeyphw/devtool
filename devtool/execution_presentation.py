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

    Kept as a factory so native campaign validation can exercise the real widget
    tree with ``App.run_test`` / ``Pilot`` without starting a second terminal
    driver. The source is the queue-backed presenter and remains non-authoritative.
    """

    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import Footer, Header, ProgressBar, RichLog, Static

    class ExecutionProgressApp(App):
        TITLE = "Devtool"
        SUB_TITLE = "Execution"
        BINDINGS = [
            ("ctrl+c", "request_cancel", "Cancel"),
            ("l", "toggle_logs", "Logs"),
        ]
        CSS = """
        #execution-root { height: 1fr; }
        #execution-status { height: auto; padding: 0 1; text-style: bold; }
        #execution-detail { height: auto; padding: 0 1; }
        #execution-progress { margin: 0 1; }
        #execution-log { height: 1fr; border-top: solid $surface-lighten-2; }
        #execution-cancel { height: auto; padding: 0 1; color: $warning; }
        """

        def __init__(self) -> None:
            super().__init__()
            self._cancel_armed_until = 0.0
            self._logs_visible = True

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical(id="execution-root"):
                yield Static("Starting…", id="execution-status")
                yield ProgressBar(total=100, show_eta=False, id="execution-progress")
                yield Static("Preparing command", id="execution-detail")
                yield Static("", id="execution-cancel")
                yield RichLog(id="execution-log", wrap=True, highlight=False, markup=False, max_lines=source.max_log_lines)
            yield Footer()

        def on_mount(self) -> None:
            source._ready.set()
            self.set_interval(1 / 12, self._drain_updates)

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
            command = str(snapshot.get("command") or "Command")
            phase = str(snapshot.get("phase_name") or "")
            target = str(snapshot.get("target") or "")
            title = command if not phase or phase == command else f"{command} · {phase}"
            if target:
                title += f" · {target}"
            self.query_one("#execution-status", Static).update(title)
            detail = str(snapshot.get("detail") or snapshot.get("completion_status") or "Working…")
            self.query_one("#execution-detail", Static).update(detail)
            percent = snapshot.get("percent")
            completed = snapshot.get("completed")
            total = snapshot.get("total")
            if percent is None and isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total:
                percent = float(completed) * 100.0 / float(total)
            bar = self.query_one("#execution-progress", ProgressBar)
            if isinstance(percent, (int, float)):
                bar.update(progress=max(0.0, min(100.0, float(percent))))

        def action_toggle_logs(self) -> None:
            log = self.query_one("#execution-log", RichLog)
            self._logs_visible = not self._logs_visible
            log.display = self._logs_visible

        def action_request_cancel(self) -> None:
            now = time.monotonic()
            notice = self.query_one("#execution-cancel", Static)
            if now <= self._cancel_armed_until:
                self._cancel_armed_until = 0.0
                notice.update("Cancelling current command…")
                source._request_cancel()
                return
            self._cancel_armed_until = now + 3.0
            notice.update("Press Ctrl+C again within 3s to cancel the current command")

    return ExecutionProgressApp()

class _TextualExecutionPresenter:
    """Thread-owned Textual progress surface fed by a bounded message queue."""

    interactive = True
    hides_raw_output = True

    def __init__(self, *, max_log_lines: int = 400) -> None:
        self.max_log_lines = max(50, int(max_log_lines))
        self._queue: Queue[tuple[str, object]] = Queue(maxsize=max(256, self.max_log_lines * 2))
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._failed: BaseException | None = None
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
        if self._thread is not None:
            if snapshot:
                self.update(snapshot)
            return
        if snapshot:
            self._offer("snapshot", dict(snapshot))
        self._thread = threading.Thread(target=self._run, name="devtool-textual-execution", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=0.75):
            self._offer("stop", None)
            raise RuntimeError("Textual execution presenter did not become ready")
        if self._failed is not None:
            raise RuntimeError(f"Textual execution presenter failed to start: {self._failed}")

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
        if self._thread is None:
            return
        self._offer("stop", None)
        self._stopped.wait(timeout=1.5)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._thread = None

    def _run(self) -> None:  # pragma: no cover - native Textual Pilot coverage exercises the app factory
        try:
            build_textual_execution_app(self).run()
        except BaseException as exc:
            self._failed = exc
            self._ready.set()
        finally:
            self._stopped.set()


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
