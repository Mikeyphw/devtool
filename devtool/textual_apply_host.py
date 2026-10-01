"""Main-thread Textual host for interactive artifact application.

PRES-H02 closes the runtime hole exposed by native Termux after PRES-H01:
Textual's Linux driver must own the main interpreter thread, while Devtool's
artifact apply/validation lifecycle may contain blocking work and subprocesses.
The host therefore keeps Textual in the parent/main process and executes the
canonical CLI invocation in a managed child process. The child remains the sole
authority for transactions, validation, rollback, commit, RUN evidence and live
Tasker state; this module is only a presentation/control projection.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

from .tasker import DEFAULT_TASKER_DIR, resolve_cli_path

_TRUE = {"1", "true", "yes", "on"}
_APPLY_COMMANDS = {"apply-overlay", "ao", "apply-overlays", "aos", "apply-snapshot", "as"}
_CHILD_ENV = "DEVTOOL_TEXTUAL_APPLY_CHILD"
_DISABLE_ENV = "DEVTOOL_TEXTUAL_APPLY_HOST"


@dataclass(frozen=True, slots=True)
class ApplyTextualHostDecision:
    enabled: bool
    reason: str


def _isatty(stream: object) -> bool:
    try:
        return bool(getattr(stream, "isatty")())
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def textual_available() -> bool:
    try:
        return importlib.util.find_spec("textual") is not None
    except (ImportError, AttributeError, ValueError):
        return False


def select_apply_textual_host(
    args: Any,
    *,
    universal_json: bool = False,
    env: Mapping[str, str] | None = None,
    stdin: object | None = None,
    stdout: object | None = None,
    textual_probe: Callable[[], bool] | None = None,
) -> ApplyTextualHostDecision:
    """Decide whether this invocation should be hosted by Textual.

    The host is intentionally human/TTY-only. Machine output, redirected output,
    CI, verbose/trace debugging and an explicit plain presentation request remain
    ordinary CLI execution paths.
    """

    environment = env if env is not None else os.environ
    command = str(getattr(args, "command", "") or "").strip()
    if command not in _APPLY_COMMANDS:
        return ApplyTextualHostDecision(False, "not an artifact apply command")
    if str(environment.get(_CHILD_ENV, "")).strip().lower() in _TRUE:
        return ApplyTextualHostDecision(False, "managed apply child process")
    host_setting = str(environment.get(_DISABLE_ENV, "auto") or "auto").strip().lower()
    if host_setting in {"0", "false", "no", "off", "plain", "disabled"}:
        return ApplyTextualHostDecision(False, "Textual apply host disabled")
    if universal_json or bool(getattr(args, "_devtool_json", False)) or bool(getattr(args, "json", False)):
        return ApplyTextualHostDecision(False, "machine JSON output requested")
    if bool(getattr(args, "quiet", False)):
        return ApplyTextualHostDecision(False, "quiet output requested")
    if bool(getattr(args, "verbose", False)) or bool(getattr(args, "trace", False)):
        return ApplyTextualHostDecision(False, "verbose/trace output requested")
    if str(getattr(args, "stdout_payload", "auto") or "auto").strip().lower() != "auto":
        return ApplyTextualHostDecision(False, "explicit stdout payload requested")
    if str(environment.get("CI", "")).strip().lower() in _TRUE:
        return ApplyTextualHostDecision(False, "CI environment")
    if str(environment.get("DEVTOOL_EXECUTION_PRESENTATION", "auto") or "auto").strip().lower() == "plain":
        return ApplyTextualHostDecision(False, "plain execution presentation requested")
    in_stream = sys.stdin if stdin is None else stdin
    out_stream = sys.stdout if stdout is None else stdout
    if not _isatty(in_stream) or not _isatty(out_stream):
        return ApplyTextualHostDecision(False, "non-interactive terminal")
    probe = textual_probe or textual_available
    if not probe():
        return ApplyTextualHostDecision(False, "Textual package unavailable")
    return ApplyTextualHostDecision(True, "interactive artifact apply with Textual available")


def _raw_has_flag(raw_argv: Sequence[str], *names: str) -> bool:
    wanted = set(names)
    return any(str(token).split("=", 1)[0] in wanted for token in raw_argv)


def child_argv(raw_argv: Sequence[str], *, force_yes: bool) -> list[str]:
    """Return argv for the canonical managed child invocation."""

    argv = [str(token) for token in raw_argv]
    if force_yes and not _raw_has_flag(argv, "--yes", "-y", "--force"):
        # Global options may precede every command and avoid worker-thread stdin.
        argv.insert(0, "--yes")
    return argv


def _artifact_path_from_args(args: Any) -> str:
    raw = getattr(args, "artifact", "")
    if isinstance(raw, (list, tuple)):
        return " ".join(str(item) for item in raw)
    return str(raw or "")


def artifact_review_snapshot(args: Any) -> dict[str, Any]:
    """Best-effort, read-only review used before the child starts.

    Canonical qualification still happens exclusively in the managed child.
    """

    path_text = _artifact_path_from_args(args)
    review: dict[str, Any] = {
        "artifact": Path(path_text).name if path_text and " " not in path_text else path_text,
        "path": path_text,
        "command": str(getattr(args, "command", "") or "apply-overlay"),
        "target": str(getattr(args, "target", "") or ""),
        "changes": "qualification pending",
        "validation": "qualification pending",
        "lifecycle": "qualification pending",
        "apply": "atomic lifecycle",
    }
    if not path_text or " " in path_text:
        return review
    try:
        from .artifacts import read_artifact_manifest, resolve_artifact_path

        artifact_path = resolve_artifact_path(path_text)
        manifest = read_artifact_manifest(artifact_path)
        if not isinstance(manifest, dict):
            return review
        inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
        entries = inventory.get("entries") if isinstance(inventory.get("entries"), list) else []
        actions = sorted({str(item.get("action") or "change") for item in entries if isinstance(item, dict)})
        validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
        apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
        target = str(manifest.get("target") or review["target"] or "")
        lifecycle_bits: list[str] = []
        script = validation.get("script") if isinstance(validation.get("script"), dict) else {}
        if script:
            lifecycle_bits.append("validator")
        tests = validation.get("tests") if isinstance(validation.get("tests"), list) else []
        if tests:
            lifecycle_bits.append(f"{len(tests)} tests")
        apply_bits: list[str] = []
        if "atomic" in apply:
            apply_bits.append("atomic" if bool(apply.get("atomic")) else "non-atomic")
        conflict = str(apply.get("conflict_policy") or "").strip()
        if conflict:
            apply_bits.append(conflict)
        if bool(apply.get("commit")):
            apply_bits.append("commit")
        review.update({
            "artifact": str(manifest.get("name") or review["artifact"]),
            "target": target,
            "changes": f"{len(entries)} files" + (f" · {', '.join(actions)}" if actions else ""),
            "validation": "required" if bool(validation.get("required")) else "optional",
            "lifecycle": " · ".join(lifecycle_bits) or "standard",
            "apply": " · ".join(apply_bits) or "standard",
        })
    except Exception:
        # The managed child owns authoritative validation and will report errors.
        pass
    return review


def apply_phase_text(state: Mapping[str, Any]) -> str | None:
    """Return meaningful canonical phase text, or None when state omits it.

    Final Tasker/RUN updates may intentionally carry only status/progress.  The
    presentation must preserve the previous detailed phase in that case rather
    than replace it with a generic placeholder.
    """

    phase = state.get("phase")
    if isinstance(phase, Mapping):
        phase_name = str(phase.get("name") or phase.get("label") or "")
        detail = str(phase.get("detail") or "")
    else:
        phase_name = str(phase or "")
        detail = ""
    progress = state.get("progress") if isinstance(state.get("progress"), Mapping) else {}
    if not detail:
        detail = str(progress.get("detail") or "")
    text = " · ".join(value for value in (phase_name, detail) if value)
    return text or None


class ArtifactApplyHostSource:
    """Managed-child source consumed by the main-thread Textual app."""

    def __init__(
        self,
        raw_argv: Sequence[str],
        args: Any,
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        max_log_lines: int = 1200,
        live_state_dir: Path | None = None,
    ) -> None:
        self.raw_argv = tuple(str(item) for item in raw_argv)
        self.args = args
        self.env = dict(env if env is not None else os.environ)
        self.cwd = Path(cwd or Path.cwd())
        self.popen_factory = popen_factory
        self.max_log_lines = max(100, int(max_log_lines))
        self._live_state_dir = Path(live_state_dir) if live_state_dir is not None else None
        self.run_id = uuid.uuid4().hex
        self.review = artifact_review_snapshot(args)
        self._lines: queue.Queue[str] = queue.Queue(maxsize=max(512, self.max_log_lines * 2))
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._started = False
        self._cancel_requested = False
        self._last_state: dict[str, Any] = {}
        self._returncode: int | None = None
        self._started_at = 0.0

    @property
    def state_dir(self) -> Path:
        return self._live_state_dir or resolve_cli_path(DEFAULT_TASKER_DIR)

    @property
    def run_state_file(self) -> Path:
        return self.state_dir / "runs" / f"{self.run_id}.json"

    @property
    def started(self) -> bool:
        return self._started

    @property
    def returncode(self) -> int | None:
        proc = self._process
        if proc is not None and self._returncode is None:
            value = proc.poll()
            if value is not None:
                self._returncode = int(value)
        return self._returncode

    @property
    def running(self) -> bool:
        return bool(self._started and self.returncode is None)

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def _offer_line(self, line: str) -> None:
        text = str(line or "").rstrip("\r\n")
        if not text:
            return
        try:
            self._lines.put_nowait(text)
        except queue.Full:
            try:
                self._lines.get_nowait()
            except queue.Empty:
                pass
            try:
                self._lines.put_nowait(text)
            except queue.Full:
                pass

    def start(self, *, force_yes: bool) -> None:
        if self._started:
            return
        child_env = dict(self.env)
        child_env[_CHILD_ENV] = "1"
        child_env["DEVTOOL_AUTO_HUD_CHILD"] = "1"
        child_env["DEVTOOL_RUN_ID"] = self.run_id
        # Nested runner/hook presentation remains plain; the parent owns the TTY.
        child_env["DEVTOOL_EXECUTION_PRESENTATION"] = "plain"
        argv = [sys.executable, "-m", "devtool", *child_argv(self.raw_argv, force_yes=force_yes)]
        self._process = self.popen_factory(
            argv,
            cwd=str(self.cwd),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._started = True
        self._started_at = time.monotonic()

        def _reader_loop() -> None:
            proc = self._process
            if proc is None or proc.stdout is None:
                return
            try:
                for line in proc.stdout:
                    self._offer_line(line)
            finally:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

        self._reader = threading.Thread(target=_reader_loop, name=f"devtool-apply-host-reader-{self.run_id[:8]}", daemon=True)
        self._reader.start()

    def drain_lines(self, *, limit: int = 256) -> list[str]:
        lines: list[str] = []
        for _ in range(max(1, int(limit))):
            try:
                lines.append(self._lines.get_nowait())
            except queue.Empty:
                break
        return lines

    def read_state(self) -> dict[str, Any]:
        path = self.run_state_file
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._last_state = payload
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return dict(self._last_state)

    def request_cancel(self) -> None:
        proc = self._process
        if proc is None or proc.poll() is not None:
            return
        self._cancel_requested = True
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except Exception:
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                pass

    def terminate(self) -> None:
        proc = self._process
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def wait(self, timeout: float | None = None) -> int | None:
        proc = self._process
        if proc is None:
            return self._returncode
        try:
            self._returncode = int(proc.wait(timeout=timeout))
        except subprocess.TimeoutExpired:
            return None
        return self._returncode

    def final_summary(self) -> tuple[str, str]:
        state = self.read_state()
        log_path = str(state.get("log_path") or "")
        status = str(state.get("status") or "")
        return status, log_path


def build_apply_textual_app(source: ArtifactApplyHostSource, *, confirm_required: bool) -> Any:
    """Build the real main-thread Textual apply host."""

    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.css.query import NoMatches
    from textual.screen import ModalScreen
    from textual.widgets import Button, Footer, Header, ProgressBar, RichLog, Static

    class ApplyConfirmationModal(ModalScreen[bool]):
        DEFAULT_CSS = """
        ApplyConfirmationModal { align: center middle; }
        #apply-confirm-box { width: min(72, 92%); height: auto; border: round $warning; padding: 1 2; background: $surface; }
        #apply-confirm-actions { height: auto; align-horizontal: right; margin-top: 1; }
        #apply-confirm-actions Button { margin-left: 1; }
        """

        def compose(self) -> ComposeResult:
            with Vertical(id="apply-confirm-box"):
                yield Static("Apply this artifact?", id="apply-confirm-title")
                yield Static(source.review.get("artifact") or source.review.get("path") or "artifact")
                with Horizontal(id="apply-confirm-actions"):
                    yield Button("Cancel", id="apply-confirm-no")
                    yield Button("Apply", variant="warning", id="apply-confirm-yes")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "apply-confirm-yes")

    class ApplyCancelModal(ModalScreen[bool]):
        DEFAULT_CSS = ApplyConfirmationModal.DEFAULT_CSS

        def compose(self) -> ComposeResult:
            with Vertical(id="apply-confirm-box"):
                yield Static("Cancel the active artifact apply?", id="apply-confirm-title")
                yield Static("Devtool will signal the managed apply process and preserve canonical rollback/recovery behavior.")
                with Horizontal(id="apply-confirm-actions"):
                    yield Button("Keep running", id="apply-confirm-no")
                    yield Button("Cancel apply", variant="error", id="apply-confirm-yes")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "apply-confirm-yes")

    class ArtifactApplyApp(App[int]):
        TITLE = "Devtool"
        SUB_TITLE = "Artifact apply"
        BINDINGS = [
            ("ctrl+c", "request_cancel", "Cancel"),
            ("l", "toggle_logs", "Logs"),
        ]
        CSS = """
        #apply-root { height: 1fr; }
        #apply-review { height: auto; border: round $accent; padding: 0 1; margin: 0 1; }
        #apply-status { height: auto; padding: 0 1; text-style: bold; }
        #apply-phase { height: auto; padding: 0 1; }
        #apply-progress { margin: 0 1; }
        #apply-log { height: 1fr; border-top: solid $surface-lighten-2; }
        #apply-notice { height: auto; min-height: 1; padding: 0 1; color: $warning; }
        .narrow #apply-review { margin: 0; border: none; border-bottom: solid $accent; }
        .narrow #apply-progress { margin: 0; }
        """

        def __init__(self) -> None:
            super().__init__()
            self._logs_visible = True
            self._finish_seen_at: float | None = None
            self._started_child = False
            self._poll_timer: Any | None = None

        def compose(self) -> ComposeResult:
            review = source.review
            review_text = (
                f"Artifact   {review.get('artifact') or '-'}\n"
                f"Target     {review.get('target') or '-'}\n"
                f"Changes    {review.get('changes') or '-'}\n"
                f"Validate   {review.get('validation') or '-'}\n"
                f"Lifecycle  {review.get('lifecycle') or '-'}\n"
                f"Apply      {review.get('apply') or '-'}"
            )
            yield Header()
            with Vertical(id="apply-root"):
                yield Static(review_text, id="apply-review")
                yield Static("Waiting to start…", id="apply-status")
                yield ProgressBar(total=100, show_eta=False, id="apply-progress")
                yield Static("Canonical artifact qualification pending", id="apply-phase")
                yield Static("", id="apply-notice")
                yield RichLog(id="apply-log", wrap=True, highlight=False, markup=False, max_lines=source.max_log_lines)
            yield Footer()

        def _apply_responsive_width(self, width: int) -> None:
            # Textual 8.x does not support the CSS media-query syntax used here. Keep responsive
            # state in the real widget tree instead of relying on unsupported
            # stylesheet syntax. The Screen owns the class so descendant
            # selectors apply to every artifact-host widget consistently.
            screen = self.screen
            if int(width) < 70:
                screen.add_class("narrow")
            else:
                screen.remove_class("narrow")

        def on_mount(self) -> None:
            self._apply_responsive_width(self.size.width)
            self._poll_timer = self.set_interval(0.10, self._poll_child)
            if confirm_required:
                self.push_screen(ApplyConfirmationModal(), self._after_initial_confirmation)
            else:
                self._start_child(force_yes=True)

        def on_resize(self, event: Any) -> None:
            self._apply_responsive_width(event.size.width)

        def _after_initial_confirmation(self, confirmed: bool | None) -> None:
            if not confirmed:
                self.exit(1)
                return
            self._start_child(force_yes=True)

        def _start_child(self, *, force_yes: bool) -> None:
            if self._started_child:
                return
            self._started_child = True
            self.query_one("#apply-status", Static).update("Starting canonical Devtool apply…")
            try:
                source.start(force_yes=force_yes)
            except Exception as exc:
                self.query_one("#apply-status", Static).update("Failed to start artifact apply")
                self.query_one("#apply-log", RichLog).write(f"{type(exc).__name__}: {exc}")
                self._finish_seen_at = time.monotonic()

        def _apply_state(self, state: Mapping[str, Any]) -> None:
            status = str(state.get("status") or "running")
            message = str(state.get("message") or "Working…")
            self.query_one("#apply-status", Static).update(f"{status.upper()} · {message}")
            progress = state.get("progress") if isinstance(state.get("progress"), Mapping) else {}
            # Preserve the last meaningful phase when a terminal state omits
            # phase metadata. Canonical live-state writers are allowed to emit
            # a compact final status/progress record.
            phase_line = apply_phase_text(state)
            if phase_line is not None:
                self.query_one("#apply-phase", Static).update(phase_line)
            percent = progress.get("percent")
            if percent is None:
                current = state.get("current")
                total = state.get("total")
                if isinstance(current, (int, float)) and isinstance(total, (int, float)) and total:
                    percent = 100.0 * float(current) / float(total)
            if isinstance(percent, (int, float)):
                self.query_one("#apply-progress", ProgressBar).update(progress=max(0.0, min(100.0, float(percent))))

        def _stop_polling(self) -> None:
            timer = self._poll_timer
            if timer is not None:
                try:
                    timer.stop()
                finally:
                    self._poll_timer = None

        def _poll_child(self) -> None:
            # Textual may deliver one final interval tick while run_test()/App.exit()
            # is tearing down the default Screen. Any widget query in this whole
            # poll can therefore race with child detachment, not only the first
            # query. Treat NoMatches as normal shutdown and stop polling.
            try:
                log = self.query_one("#apply-log", RichLog)
                for line in source.drain_lines():
                    if self._logs_visible:
                        log.write(line)
                state = source.read_state()
                if state:
                    self._apply_state(state)
                rc = source.returncode
                if rc is None:
                    return
                # Drain any final child output before restoring the terminal.
                for line in source.drain_lines():
                    if self._logs_visible:
                        log.write(line)
                if self._finish_seen_at is None:
                    self._finish_seen_at = time.monotonic()
                    label = "completed successfully" if rc == 0 else ("cancelled" if rc == 130 else f"failed · exit {rc}")
                    self.query_one("#apply-status", Static).update(f"Devtool {label}")
                    if rc == 0:
                        self.query_one("#apply-progress", ProgressBar).update(progress=100.0)
                    return
                if time.monotonic() - self._finish_seen_at >= 0.35:
                    self._stop_polling()
                    self.exit(int(rc))
            except NoMatches:
                self._stop_polling()
                return

        def action_toggle_logs(self) -> None:
            log = self.query_one("#apply-log", RichLog)
            self._logs_visible = not self._logs_visible
            log.display = self._logs_visible

        def action_request_cancel(self) -> None:
            if not source.running:
                return
            self.push_screen(ApplyCancelModal(), self._after_cancel_confirmation)

        def _after_cancel_confirmation(self, confirmed: bool | None) -> None:
            if not confirmed:
                return
            source.request_cancel()
            self.query_one("#apply-notice", Static).update("Cancellation requested; waiting for Devtool recovery/rollback…")

        def on_unmount(self) -> None:
            self._stop_polling()
            if source.running:
                source.terminate()

    return ArtifactApplyApp()


def run_apply_textual_host(raw_argv: Sequence[str], args: Any) -> int:
    """Run one interactive artifact apply under a main-thread Textual host."""

    source = ArtifactApplyHostSource(raw_argv, args)
    confirm_required = not bool(getattr(args, "yes", False) or getattr(args, "force", False))
    app = build_apply_textual_app(source, confirm_required=confirm_required)
    result = app.run()
    rc = int(result if isinstance(result, int) else (source.wait(timeout=0.1) or 0))
    status, log_path = source.final_summary()
    # Keep the post-TUI terminal useful for shell history / support bundles.
    if rc == 0:
        print("Devtool completed successfully.")
    elif rc == 130:
        print("Devtool cancelled.")
    else:
        print("Devtool failed.")
    print(f"Run: {source.run_id}")
    if log_path:
        print(f"Log: {log_path}")
    if status and status not in {"ok", "running"}:
        print(f"Status: {status}")
    return rc


__all__ = [
    "ApplyTextualHostDecision",
    "ArtifactApplyHostSource",
    "artifact_review_snapshot",
    "build_apply_textual_app",
    "child_argv",
    "run_apply_textual_host",
    "select_apply_textual_host",
    "textual_available",
]
