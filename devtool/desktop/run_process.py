from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .gtk_runtime import gtk_log_dir, redact_text

OutputCallback = Callable[..., None]
ExitCallback = Callable[["DesktopRunHandle"], None]

_PHASE_RE = re.compile(r"\b(?:phase|stage|task)\s*[:=]\s*(?P<name>[^\r\n]{1,120})", re.IGNORECASE)
_ERROR_RE = re.compile(r"\b(error|failed|failure|exception|traceback)\b", re.IGNORECASE)
_WARNING_RE = re.compile(r"\b(warning|warn)\b", re.IGNORECASE)
MAX_OUTPUT_LINES = 400
MAX_CHANNEL_LINES = 200
MAX_DIAGNOSTICS = 80
MAX_LOG_LINE_CHARS = 2_000


def _bounded_append(values: list[Any], value: Any, limit: int) -> None:
    values.append(value)
    overflow = len(values) - max(0, limit)
    if overflow > 0:
        del values[:overflow]


def _trim_log_line(line: str) -> str:
    return line if len(line) <= MAX_LOG_LINE_CHARS else line[:MAX_LOG_LINE_CHARS] + "…<truncated>"


@dataclass(slots=True)
class DesktopRunHandle:
    """Runtime identity for a Control Center-launched Devtool process."""

    run_id: str
    argv: list[str]
    cwd: Path
    environment: dict[str, str]
    log_path: Path
    process: subprocess.Popen[str]
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    returncode: int | None = None
    status: str = "running"
    output_lines: list[str] = field(default_factory=list)
    stdout_lines: list[str] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, str]] = field(default_factory=list)
    current_phase: str = ""

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def elapsed_seconds(self) -> float:
        return float((self.ended_at or time.time()) - self.started_at)

    def summary(self) -> str:
        rc = self.returncode if self.returncode is not None else self.process.poll()
        phase = f" · {self.current_phase}" if self.current_phase else ""
        elapsed = f" · {self.elapsed_seconds:.1f}s"
        if rc is None:
            return f"{self.run_id} · running{phase}{elapsed}"
        return f"{self.run_id} · exit {rc}{phase}{elapsed}"

    def failure_summary(self) -> str:
        for item in reversed(self.diagnostics):
            if item.get("severity") == "error":
                return item.get("message") or f"Run exited {self.returncode}"
        if self.returncode and self.returncode != 0:
            return f"Desktop-launched run exited {self.returncode}."
        return ""


class DesktopRunLauncher:
    """Start and control desktop app launches without a shell trampoline.

    The Control Center owns command construction through the argv-first workflow
    model. This launcher preserves that property at execution time: no
    ``sh -lc`` wrapper is used, stdout and stderr stay distinct, and a stable
    run id plus log file survive after the UI closes.
    """

    def __init__(self, *, state_dir: Path | str | None = None) -> None:
        self.state_dir = Path(state_dir).expanduser() if state_dir is not None else gtk_log_dir() / "control-center-runs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handles: dict[str, DesktopRunHandle] = {}

    def start(
        self,
        argv: list[str],
        *,
        cwd: Path | str,
        environment: Mapping[str, str] | None = None,
        on_output: OutputCallback | None = None,
        on_exit: ExitCallback | None = None,
    ) -> DesktopRunHandle:
        clean_argv = [str(part) for part in argv if str(part)]
        if not clean_argv:
            raise ValueError("Cannot launch an empty command argv.")
        run_id = f"desktop-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        cwd_path = Path(cwd).expanduser().resolve()
        if not cwd_path.is_dir():
            raise FileNotFoundError(f"Run cwd does not exist: {cwd_path}")
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in (environment or {}).items() if str(key)})
        env["DEVTOOL_CONTROL_CENTER_ACTIVE"] = "1"
        env["DEVTOOL_DESKTOP_RUN_ID"] = run_id
        log_path = self.state_dir / f"{run_id}.log"
        with log_path.open("w", encoding="utf-8") as handle:
            handle.write(redact_text("$ " + " ".join(clean_argv)) + "\n")
            handle.write(f"cwd={redact_text(cwd_path)}\nrun_id={run_id}\n\n")
        process = subprocess.Popen(
            clean_argv,
            cwd=str(cwd_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        handle = DesktopRunHandle(
            run_id=run_id,
            argv=clean_argv,
            cwd=cwd_path,
            environment={str(key): str(value) for key, value in (environment or {}).items() if str(key)},
            log_path=log_path,
            process=process,
        )
        with self._lock:
            self._handles[run_id] = handle
        threading.Thread(
            target=self._pump_output,
            args=(handle, on_output, on_exit),
            name=f"devtool-control-center-run-{run_id}",
            daemon=True,
        ).start()
        return handle

    def _pump_output(self, handle: DesktopRunHandle, on_output: OutputCallback | None, on_exit: ExitCallback | None) -> None:
        output_queue: queue.Queue[tuple[str, str] | None] = queue.Queue()

        def reader(channel: str, stream: Any) -> None:
            try:
                if stream is not None:
                    for raw_line in stream:
                        output_queue.put((channel, redact_text(raw_line.rstrip("\n"))))
            finally:
                output_queue.put(None)

        readers = [
            threading.Thread(target=reader, args=("stdout", handle.process.stdout), daemon=True),
            threading.Thread(target=reader, args=("stderr", handle.process.stderr), daemon=True),
        ]
        for thread in readers:
            thread.start()

        finished_readers = 0
        try:
            while finished_readers < len(readers):
                item = output_queue.get()
                if item is None:
                    finished_readers += 1
                    continue
                channel, line = item
                clean_line = _trim_log_line(line)
                self._record_output(handle, channel, clean_line)
                self._notify_output(on_output, channel, clean_line)
            handle.returncode = handle.process.wait()
            handle.ended_at = time.time()
            handle.status = "completed" if handle.returncode == 0 else "failed"
            with handle.log_path.open("a", encoding="utf-8") as log:
                log.write(f"\nexit={handle.returncode}\nelapsed_seconds={handle.elapsed_seconds:.3f}\n")
        finally:
            if on_exit is not None:
                on_exit(handle)

    def _record_output(self, handle: DesktopRunHandle, channel: str, line: str) -> None:
        clean_line = _trim_log_line(line)
        entry = f"[{channel}] {clean_line}"
        _bounded_append(handle.output_lines, entry, MAX_OUTPUT_LINES)
        if channel == "stderr":
            _bounded_append(handle.stderr_lines, clean_line, MAX_CHANNEL_LINES)
        else:
            _bounded_append(handle.stdout_lines, clean_line, MAX_CHANNEL_LINES)
        phase_match = _PHASE_RE.search(clean_line)
        if phase_match:
            handle.current_phase = phase_match.group("name").strip()
        severity = "error" if _ERROR_RE.search(clean_line) else "warning" if _WARNING_RE.search(clean_line) else ""
        if severity:
            diagnostic = {"severity": severity, "message": clean_line[:500], "source": channel}
            if diagnostic not in handle.diagnostics:
                _bounded_append(handle.diagnostics, diagnostic, MAX_DIAGNOSTICS)
        with handle.log_path.open("a", encoding="utf-8") as log:
            log.write(entry + "\n")

    def _notify_output(self, on_output: OutputCallback | None, channel: str, line: str) -> None:
        if on_output is None:
            return
        try:
            on_output(channel, line)
        except TypeError:
            on_output(f"[{channel}] {line}")

    def get(self, run_id: str) -> DesktopRunHandle | None:
        with self._lock:
            return self._handles.get(run_id)

    def active(self) -> list[DesktopRunHandle]:
        with self._lock:
            return [handle for handle in self._handles.values() if handle.running]

    def cancel(self, handle_or_id: DesktopRunHandle | str, *, force: bool = False) -> bool:
        handle = self.get(handle_or_id) if isinstance(handle_or_id, str) else handle_or_id
        if handle is None or not handle.running:
            return False
        signal_number = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(handle.process.pid, signal_number)
        except Exception:
            try:
                if force:
                    handle.process.kill()
                else:
                    handle.process.terminate()
            except Exception:
                return False
        handle.status = "force-cancelling" if force else "cancelling"
        with handle.log_path.open("a", encoding="utf-8") as log:
            log.write(f"\nrequested_{'force_' if force else ''}cancel=1\n")
        return True
