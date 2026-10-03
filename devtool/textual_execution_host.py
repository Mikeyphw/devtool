"""Main-thread Textual host for ordinary interactive Devtool execution.

PRES-R03 completes the R01 ownership migration without moving execution truth
into the UI.  The parent/main interpreter owns Textual's terminal driver; the
canonical Devtool invocation runs as a managed child.  The host projects the
child's bounded stdout plus existing Tasker live state into the R01 execution
app, and cancellation is delegated to the child process group.
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

from .execution_presentation import build_textual_execution_app, create_textual_execution_source
from .tasker import DEFAULT_TASKER_DIR, resolve_cli_path
from .invocation_origin import create_managed_run_handoff

_TRUE = {"1", "true", "yes", "on"}
_CHILD_ENV = "DEVTOOL_TEXTUAL_EXECUTION_CHILD"
_DISABLE_ENV = "DEVTOOL_TEXTUAL_EXECUTION_HOST"
# Artifact apply has its own H02 host because it needs pre-apply review and
# confirmation.  These are the ordinary work commands that own live progress.
_EXECUTION_COMMANDS = {
    "restore", "rs", "build", "b", "test", "t", "lint", "validate", "v", "validate-all", "invoke",
    "wine-validate", "wv", "start-validation", "sv", "resume-validation", "rv", "run", "rn",
    "package", "pkg", "clean", "cl", "batch", "ci", "oci",
}


@dataclass(frozen=True, slots=True)
class ExecutionTextualHostDecision:
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


def select_execution_textual_host(
    args: Any,
    *,
    universal_json: bool = False,
    env: Mapping[str, str] | None = None,
    stdin: object | None = None,
    stdout: object | None = None,
    textual_probe: Callable[[], bool] | None = None,
) -> ExecutionTextualHostDecision:
    environment = env if env is not None else os.environ
    command = str(getattr(args, "command", "") or "").strip()
    if command not in _EXECUTION_COMMANDS:
        return ExecutionTextualHostDecision(False, "not a normal interactive execution command")
    if str(environment.get(_CHILD_ENV, "")).strip().lower() in _TRUE:
        return ExecutionTextualHostDecision(False, "managed execution child process")
    setting = str(environment.get(_DISABLE_ENV, "auto") or "auto").strip().lower()
    if setting in {"0", "false", "no", "off", "plain", "disabled"}:
        return ExecutionTextualHostDecision(False, "Textual execution host disabled")
    if universal_json or bool(getattr(args, "_devtool_json", False)) or bool(getattr(args, "json", False)):
        return ExecutionTextualHostDecision(False, "machine JSON output requested")
    if bool(getattr(args, "quiet", False)):
        return ExecutionTextualHostDecision(False, "quiet output requested")
    if bool(getattr(args, "verbose", False)) or bool(getattr(args, "trace", False)):
        return ExecutionTextualHostDecision(False, "verbose/trace output requested")
    if str(getattr(args, "stdout_payload", "auto") or "auto").strip().lower() != "auto":
        return ExecutionTextualHostDecision(False, "explicit stdout payload requested")
    if str(environment.get("CI", "")).strip().lower() in _TRUE:
        return ExecutionTextualHostDecision(False, "CI environment")
    presentation = str(environment.get("DEVTOOL_EXECUTION_PRESENTATION", "auto") or "auto").strip().lower()
    if presentation == "plain":
        return ExecutionTextualHostDecision(False, "plain execution presentation requested")
    in_stream = sys.stdin if stdin is None else stdin
    out_stream = sys.stdout if stdout is None else stdout
    if not _isatty(in_stream) or not _isatty(out_stream):
        return ExecutionTextualHostDecision(False, "non-interactive terminal")
    probe = textual_probe or textual_available
    if not probe():
        return ExecutionTextualHostDecision(False, "Textual package unavailable")
    return ExecutionTextualHostDecision(True, "interactive normal execution with Textual available")


def _phase_snapshot(state: Mapping[str, Any], *, command: str) -> dict[str, Any]:
    phase = state.get("phase")
    if isinstance(phase, Mapping):
        phase_name = str(phase.get("name") or phase.get("label") or phase.get("display_name") or "")
        phase_detail = str(phase.get("detail") or "")
        target = str(phase.get("target") or "")
    else:
        phase_name = str(phase or state.get("phase_name") or "")
        phase_detail = ""
        target = ""
    progress = state.get("progress") if isinstance(state.get("progress"), Mapping) else {}
    detail = str(progress.get("detail") or state.get("message") or phase_detail or state.get("status") or "Working…")
    percent = progress.get("percent")
    completed = progress.get("completed", progress.get("current"))
    total = progress.get("total")
    if not target:
        target = str(progress.get("target") or state.get("target") or "")
    return {
        "command": str(state.get("command") or command or "Devtool"),
        "phase_name": phase_name,
        "target": target,
        "detail": detail,
        "completion_status": str(state.get("status") or "running"),
        "percent": percent,
        "completed": completed,
        "total": total,
    }


class ExecutionTextualHostSource:
    def __init__(
        self,
        raw_argv: Sequence[str],
        args: Any,
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        live_state_dir: Path | None = None,
        max_log_lines: int = 900,
    ) -> None:
        self.raw_argv = tuple(str(item) for item in raw_argv)
        self.args = args
        self.env = dict(env if env is not None else os.environ)
        self.cwd = Path(cwd or Path.cwd())
        self.popen_factory = popen_factory
        self.live_state_dir = Path(live_state_dir) if live_state_dir is not None else None
        self.run_id = uuid.uuid4().hex
        self.command = str(getattr(args, "command", "") or "devtool")
        self.presenter = create_textual_execution_source(max_log_lines=max_log_lines)
        self.presenter.terminal_host_ready = True
        self.presenter.set_stop_callback(self.request_cancel)
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._bridge: threading.Thread | None = None
        self._lines: queue.Queue[str] = queue.Queue(maxsize=max(512, max_log_lines * 2))
        self._returncode: int | None = None
        self._last_state: dict[str, Any] = {}
        self._cancel_requested = False

    @property
    def state_dir(self) -> Path:
        return self.live_state_dir or resolve_cli_path(DEFAULT_TASKER_DIR)

    @property
    def run_state_file(self) -> Path:
        return self.state_dir / "runs" / f"{self.run_id}.json"

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
        return bool(self._process is not None and self.returncode is None)

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

    def start(self) -> None:
        child_env = dict(self.env)
        child_env[_CHILD_ENV] = "1"
        child_env["DEVTOOL_AUTO_HUD_CHILD"] = "1"
        child_env["DEVTOOL_RUN_ID"] = self.run_id
        child_env["DEVTOOL_MANAGED_RUN_HANDOFF"] = create_managed_run_handoff(self.run_id)
        # Child runner presentation stays plain because the parent owns the TTY.
        child_env["DEVTOOL_EXECUTION_PRESENTATION"] = "plain"
        argv = [sys.executable, "-m", "devtool", *self.raw_argv]
        self._process = self.popen_factory(
            argv,
            cwd=str(self.cwd), env=child_env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True,
        )

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

        def _bridge_loop() -> None:
            last_state = ""
            while True:
                for _ in range(256):
                    try:
                        line = self._lines.get_nowait()
                    except queue.Empty:
                        break
                    self.presenter.output_line(line)
                state = self.read_state()
                if state:
                    encoded = json.dumps(state, sort_keys=True, default=str)
                    if encoded != last_state:
                        last_state = encoded
                        self.presenter.update(_phase_snapshot(state, command=self.command))
                rc = self.returncode
                if rc is not None:
                    for _ in range(512):
                        try:
                            self.presenter.output_line(self._lines.get_nowait())
                        except queue.Empty:
                            break
                    self.presenter.update({
                        "command": self.command,
                        "phase_name": "Complete" if rc == 0 else "Failed",
                        "detail": "Completed successfully" if rc == 0 else ("Cancelled" if rc == 130 else f"Exited with {rc}"),
                        "completion_status": "ok" if rc == 0 else ("cancelled" if rc == 130 else "failed"),
                        "percent": 100.0 if rc == 0 else None,
                    })
                    # Keep the completed workspace on screen until the operator
                    # explicitly closes it. The terminal snapshot above is the
                    # final presentation event; canonical execution is already done.
                    return
                time.sleep(0.08)

        self._reader = threading.Thread(target=_reader_loop, name=f"devtool-execution-reader-{self.run_id[:8]}", daemon=True)
        self._bridge = threading.Thread(target=_bridge_loop, name=f"devtool-execution-bridge-{self.run_id[:8]}", daemon=True)
        self._reader.start(); self._bridge.start()
        self.presenter.update({"command": self.command, "phase_name": "Starting", "detail": "Launching canonical Devtool execution"})

    def read_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.run_state_file.read_text(encoding="utf-8"))
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

    def log_path(self) -> str:
        state = self.read_state()
        return str(state.get("log_path") or "")


def run_execution_textual_host(raw_argv: Sequence[str], args: Any) -> int:
    source = ExecutionTextualHostSource(raw_argv, args)
    source.start()
    app = build_textual_execution_app(source.presenter)
    try:
        app.run()
    finally:
        if source.running:
            source.terminate()
    rc = int(source.wait(timeout=0.2) if source.returncode is None else source.returncode or 0)
    if rc == 0:
        print("Devtool completed successfully.")
    elif rc == 130:
        print("Devtool cancelled.")
    else:
        print("Devtool failed.")
    print(f"Run: {source.run_id}")
    log_path = source.log_path()
    if log_path:
        print(f"Log: {log_path}")
    return rc


__all__ = [
    "ExecutionTextualHostDecision", "ExecutionTextualHostSource",
    "run_execution_textual_host", "select_execution_textual_host", "textual_available",
]
