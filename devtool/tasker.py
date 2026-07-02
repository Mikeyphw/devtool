from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .console_utils import resolve_cli_path
from .models import OperationResult

ANDROID_TASKER_DIR = "/sdcard/Tasker/devtool"


def _is_termux_android() -> bool:
    """Return True only for real Termux/Android, not Linux desktops with /sdcard.

    Some desktop setups can have /sdcard mounted or symlinked, so merely
    checking Path('/sdcard').exists() makes Ubuntu/GNOME incorrectly use the
    Android Tasker directory. Prefer Termux/Android-specific environment and
    filesystem signals instead.
    """
    prefix = os.environ.get("PREFIX", "")
    termux_version = os.environ.get("TERMUX_VERSION")
    termux_app_pid = os.environ.get("TERMUX_APP_PID")
    if termux_version or termux_app_pid:
        return True
    if prefix.startswith("/data/data/com.termux/") or "com.termux" in prefix:
        return True
    if os.environ.get("ANDROID_ROOT") and Path("/system/build.prop").exists():
        return True
    return False


def default_tasker_dir() -> str:
    """Return the default devtool event directory for this platform.

    Android/Termux keeps the original Tasker path. Normal Unix desktops use
    XDG_STATE_HOME so HUDs and desktop notifications work without /sdcard.
    DEVTOOL_TASKER_DIR always wins.
    """
    override = os.environ.get("DEVTOOL_TASKER_DIR")
    if override:
        return override
    if _is_termux_android():
        return ANDROID_TASKER_DIR
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return str(Path(xdg_state) / "devtool")
    return str(Path.home() / ".local" / "state" / "devtool")


DEFAULT_TASKER_DIR = default_tasker_dir()
DEFAULT_TASKER_ACTION = "net.mph.devtool.EVENT"
DEFAULT_NOTIFICATION_ID = "7771"
DEFAULT_LOGCAT_TAG = "DEVTOOL"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write JSON and force it to disk for live HUD readers.

    Desktop HUDs and Tasker file observers can otherwise lag behind terminal
    output because the writer process has closed/replaced the file but the
    payload is still sitting in userspace/kernel buffers.  fsyncing the temp
    file before replace, and best-effort fsyncing the directory after replace,
    makes notification.json/latest_event.json reliable as a live state channel.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    if os.name == "posix":
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _progress_current_total(progress: dict[str, Any] | None) -> tuple[Any, Any]:
    progress = progress or {}
    current = progress.get("current")
    if current is None:
        current = progress.get("completed")
    return current, progress.get("total")


def _simple_notification_payload(notification: dict[str, Any], *, message: str, current: Any, total: Any) -> dict[str, Any]:
    title = str(notification.get("title") or "devtool")
    text = str(notification.get("text") or notification.get("body") or message or "running")
    expanded_title = str(notification.get("expanded_title") or title)
    expanded_text = str(notification.get("expanded_text") or notification.get("body") or text)
    return {
        "title": title,
        "text": text,
        "expanded_title": expanded_title,
        "expanded_text": expanded_text,
        "current": current,
        "total": total,
    }


def send_logcat_event(tag: str, payload: dict[str, Any]) -> bool:
    if not shutil.which("log"):
        return False
    try:
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        message = str(payload)
    return _run_quiet(["log", "-t", tag, message])


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value.strip())
    cleaned = cleaned.strip(".-")
    return cleaned or "event"


def tasker_dir_from_text(path_text: str | None) -> Path:
    return resolve_cli_path(path_text or DEFAULT_TASKER_DIR)

def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def start_auto_hud(directory: Path, *, mode: str = "desktop", poll_interval: float = 0.1) -> bool:
    """Start or reuse a detached HUD process for live progress.

    This is intentionally best-effort.  The command that emits progress must
    never fail just because the desktop/Termux HUD could not be started.
    A small pid file prevents spawning a new window for every devtool phase.
    """
    if os.environ.get("DEVTOOL_AUTO_HUD_CHILD") == "1" or os.environ.get("DEVTOOL_NO_AUTO_HUD") == "1":
        return False

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    pid_file = directory / "hud.pid"
    try:
        old_pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        old_pid = 0
    if _pid_is_alive(old_pid):
        return True

    # Do not detach a terminal HUD with no terminal to draw on.  Desktop/auto
    # mode can choose GTK/notifications/Termux GUI as available.
    chosen_mode = (mode or "desktop").strip() or "desktop"
    if chosen_mode in {"terminal", "console", "v3"} and not sys.stdout.isatty():
        chosen_mode = "desktop"

    argv = [
        sys.executable,
        "-m",
        "devtool",
        "hud",
        "--mode",
        chosen_mode,
        "--tasker-dir",
        str(directory),
        "--poll-interval",
        str(max(0.05, float(poll_interval))),
    ]
    try:
        env = os.environ.copy()
        env["DEVTOOL_AUTO_HUD_CHILD"] = "1"
        env["DEVTOOL_NO_AUTO_HUD"] = "1"
        env["PYTHONUNBUFFERED"] = "1"

        # Start the HUD through a detached shell trampoline. This avoids keeping
        # any pipe/session handle from the apply/build/test command alive, which
        # previously made the main devtool command appear to finish only after
        # closing the HUD window. The small launcher exits immediately; the HUD
        # is re-parented to the user session/init.
        quoted = " ".join(shlex.quote(part) for part in argv)
        launcher = f"exec </dev/null >/dev/null 2>&1; setsid {quoted} </dev/null >/dev/null 2>&1 & echo $!"
        completed = subprocess.run(
            ["/bin/sh", "-c", launcher],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            close_fds=True,
            env=env,
        )
        pid_text = (completed.stdout or "").strip().splitlines()[-1:]
        if pid_text:
            pid_file.write_text(pid_text[0].strip(), encoding="utf-8")
        return completed.returncode == 0
    except Exception:
        return False



def _result_status(result: OperationResult) -> str:
    if result.skipped:
        return "skipped"
    return "ok" if result.returncode == 0 else "failed"


def _result_to_json(result: OperationResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "status": _result_status(result),
        "exit_code": result.returncode,
        "duration_s": result.duration_seconds,
        "log_path": result.log_path or None,
        "failure_kind": result.failure_kind or None,
        "metadata": result.metadata or {},
        "warnings": sum(1 for issue in result.issues if issue.severity == "warning"),
        "errors": sum(1 for issue in result.issues if issue.severity == "error"),
    }


def _run_quiet(argv: list[str]) -> bool:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return False
    return completed.returncode == 0


def send_tasker_broadcast(action: str, event_file: Path, state_file: Path, event_type: str, run_id: str) -> bool:
    if not shutil.which("am"):
        return False
    return _run_quiet(
        [
            "am",
            "broadcast",
            "-a",
            action,
            "--es",
            "devtool_event",
            event_type,
            "--es",
            "devtool_run_id",
            run_id,
            "--es",
            "devtool_event_file",
            str(event_file),
            "--es",
            "devtool_state_file",
            str(state_file),
        ]
    )


def send_progress_notification(*, notification_id: str, title: str, body: str, ongoing: bool = False) -> bool:
    if not shutil.which("termux-notification"):
        return False
    argv = [
        "termux-notification",
        "--id",
        str(notification_id),
        "--title",
        title,
        "--content",
        body,
    ]
    if ongoing:
        argv.append("--ongoing")
    return _run_quiet(argv)


def _human_phase_name(value: str) -> str:
    key = (value or "").strip().lower().replace("_", "-")
    mapping = {
        "restore": "Restore",
        "build": "Build",
        "test": "Tests",
        "validate": "Validation",
        "apply-overlay": "Apply overlay",
        "apply-snapshot": "Apply snapshot",
        "commit": "Commit",
    }
    return mapping.get(key, (value or "devtool").strip().replace("-", " ").title())


def _human_detail(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    replacements = {
        "restore: determining projects": "Finding projects to restore",
        "restore: all projects up-to-date": "All projects are already restored",
        "restore completed": "Restore completed",
        "build: msbuild starting": "Starting MSBuild",
        "test: starting execution": "Starting tests",
        "all projects up-to-date": "All projects are already up to date",
    }
    if lowered in replacements:
        return replacements[lowered]
    for prefix, pretty in (("restored ", "Restored "), ("up-to-date ", "Already up to date: "), ("built ", "Built "), ("testing ", "Testing ")):
        if lowered.startswith(prefix):
            remainder = text[len(prefix):]
            # Hide noisy output paths in human UI: ``Project -> bin/...`` becomes ``Project``.
            if " -> " in remainder:
                remainder = remainder.split(" -> ", 1)[0]
            return pretty + remainder
    if lowered.startswith("tests:"):
        import re
        match = re.search(r"(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped", text, re.I)
        if match:
            passed, failed, skipped = map(int, match.groups())
            parts = [f"{passed} passed"]
            if failed:
                parts.append(f"{failed} failed")
            if skipped:
                parts.append(f"{skipped} skipped")
            return ("Tests failing: " if failed else "Tests passing: ") + ", ".join(parts)
        return text[0].upper() + text[1:]
    if lowered.startswith("a total of ") and "test files matched" in lowered:
        return "Found test files"
    return text


def _human_event_message(event_type: str, *, status: str = "", phase_name: str = "", message: str = "") -> str:
    human_phase = _human_phase_name(phase_name) if phase_name else "devtool"
    clean = _human_detail(message)
    normalized = (event_type or "").replace("_", "-")
    status_key = (status or "").lower()
    if normalized == "phase-started":
        return f"Starting {human_phase}"
    if normalized == "phase-finished":
        if status_key == "failed":
            return f"{human_phase} failed"
        if status_key == "skipped":
            return f"{human_phase} skipped"
        return f"{human_phase} completed"
    if normalized == "finished":
        return "Devtool finished successfully" if status_key == "ok" else "Devtool finished with errors"
    return clean or f"{human_phase} is running"

@dataclass(slots=True)
class TaskerProgressSink:
    directory: Path
    command: str
    repo_root: str
    repo_name: str
    target: str = ""
    enabled: bool = True
    broadcast: bool = False
    broadcast_action: str = DEFAULT_TASKER_ACTION
    notify_progress: bool = False
    notification_id: str = DEFAULT_NOTIFICATION_ID
    logcat_tag: str = DEFAULT_LOGCAT_TAG
    update_interval_s: float = 0.10
    notification_interval_s: float = 0.75
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    sequence: int = 0
    current_phase: dict[str, Any] | None = None
    started_at: str = field(default_factory=utc_now_iso)
    status: str = "running"
    last_error: str = ""
    _last_update_at: float = 0.0
    _last_notification_at: float = 0.0
    _last_state: dict[str, Any] = field(default_factory=dict)

    @property
    def state_file(self) -> Path:
        return self.directory / "state.json"

    @property
    def latest_event_file(self) -> Path:
        return self.directory / "latest_event.json"

    @property
    def notification_file(self) -> Path:
        # Simple Tasker bridge: watch this file, parse title/text/current/total.
        return self.directory / "notification.json"

    @property
    def events_jsonl_file(self) -> Path:
        return self.directory / "events.jsonl"

    @property
    def events_dir(self) -> Path:
        return self.directory / "events"

    @property
    def runs_dir(self) -> Path:
        return self.directory / "runs"

    def reset_run_files(self) -> None:
        """Clear stale HUD state/events before a new top-level devtool task.

        The desktop HUD intentionally shows recent events from events.jsonl.
        Without clearing the stream, a new command can start with old test/build
        lines and old totals, which looks like the process is still waiting on a
        previous run.  Best-effort only: progress emission must never fail just
        because cleanup is blocked by permissions.
        """
        for path in (self.state_file, self.latest_event_file, self.notification_file, self.events_jsonl_file):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.name == "events.jsonl":
                    path.write_text("", encoding="utf-8")
                elif path.exists():
                    path.unlink()
            except OSError:
                pass
        for directory in (self.events_dir,):
            try:
                if directory.exists():
                    for child in directory.iterdir():
                        if child.is_file():
                            child.unlink()
            except OSError:
                pass
        self.sequence = 0
        self.current_phase = None
        self._last_state = {}
        self._last_update_at = 0.0

    def started(self, *, message: str = "started") -> None:
        self.reset_run_files()
        self.emit("started", status="running", message=message, force=True, notify=True)

    def phase_started(self, name: str, *, index: int, total: int, argv: list[str] | None = None) -> None:
        self.current_phase = {
            "name": name,
            "index": index,
            "total": total,
            "argv": argv or [],
            "started_at": utc_now_iso(),
        }
        self.emit("phase_started", status="running", phase=self.current_phase, progress={"percent": None, "current": 0, "completed": 0, "total": None}, message=_human_event_message("phase_started", phase_name=name), force=True, notify=True)

    def progress(self, *, detail: str = "", completed: int | None = None, total: int | None = None, percent: float | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_update_at < self.update_interval_s:
            return
        self._last_update_at = now
        if percent is None and completed is not None and total:
            percent = max(0.0, min(100.0, completed / total * 100.0))
        payload = {
            "percent": percent,
            "current": completed,
            "completed": completed,
            "total": total,
            "detail": detail,
        }
        self.emit("progress", status="running", phase=self.current_phase, progress=payload, message=detail, notify=True)

    def phase_finished(self, result: OperationResult) -> None:
        status = _result_status(result)
        phase = dict(self.current_phase or {"name": result.name})
        phase["finished_at"] = utc_now_iso()
        self.emit(
            "phase_finished",
            status="running" if result.returncode == 0 else "failed",
            phase=phase,
            result=_result_to_json(result),
            message=_human_event_message("phase_finished", status=status, phase_name=result.name),
            force=True,
            notify=True,
        )

    def finished(self, *, ok: bool, totals: dict[str, int], results: list[OperationResult], notification: dict[str, Any] | None = None) -> None:
        self.status = "ok" if ok else "failed"
        self.emit(
            "finished",
            status=self.status,
            totals=totals,
            results=[_result_to_json(result) for result in results],
            notification=notification or {},
            message=_human_event_message("finished", status=self.status),
            force=True,
            notify=True,
        )

    def emit(
        self,
        event_type: str,
        *,
        status: str,
        message: str = "",
        phase: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        results: list[dict[str, Any]] | None = None,
        totals: dict[str, Any] | None = None,
        notification: dict[str, Any] | None = None,
        force: bool = False,
        notify: bool = False,
    ) -> None:
        if not self.enabled and not self.notify_progress:
            return
        self.sequence += 1
        timestamp = utc_now_iso()
        event_id = f"{self.run_id}-{self.sequence:05d}"
        if phase is not None:
            self.current_phase = phase
        current, total = _progress_current_total(progress)
        # Only progress updates may inherit the previous current/total. Phase
        # changes and final events must not reuse stale counts like 0/9 or 9/31.
        if event_type == "progress" and self._last_state:
            if current is None:
                current = self._last_state.get("current")
            if total is None:
                total = self._last_state.get("total")
        if event_type == "finished" and status == "ok" and total is not None:
            current = total
        notification_payload = notification or self._notification_payload(status, message, progress)
        simple_notification = _simple_notification_payload(notification_payload, message=message, current=current, total=total)
        state = {
            "schema_version": 1,
            "producer": "devtool",
            "run_id": self.run_id,
            "event_id": event_id,
            "event_type": event_type,
            "status": status,
            "command": self.command,
            "repo_root": self.repo_root,
            "repo_name": self.repo_name,
            "target": self.target or None,
            "started_at": self.started_at,
            "updated_at": timestamp,
            "finished_at": timestamp if event_type == "finished" else None,
            "message": message,
            "current": current,
            "total": total,
            "phase": phase if phase is not None else self.current_phase,
            "progress": progress or {},
            "result": result,
            "results": results,
            "totals": totals or {},
            "notification": notification_payload,
            "tasker_notification": simple_notification,
            "files": {
                "state": str(self.state_file),
                "latest_event": str(self.latest_event_file),
                "notification": str(self.notification_file),
                "events_jsonl": str(self.events_jsonl_file),
                "events_dir": str(self.events_dir),
            },
        }
        event = dict(state)
        event["event_file"] = None
        event_file = self.events_dir / f"{timestamp.replace(':', '').replace('.', '-')}-{self.sequence:05d}-{_safe_name(event_type)}.json"
        event["event_file"] = str(event_file)
        state["event_file"] = str(event_file)
        if self.enabled:
            try:
                _atomic_write_json(self.state_file, state)
                _atomic_write_json(self.latest_event_file, event)
                _atomic_write_json(self.notification_file, simple_notification)
                _atomic_write_json(event_file, event)
                _append_jsonl(self.events_jsonl_file, event)
                send_logcat_event(self.logcat_tag, event)
                _atomic_write_json(self.runs_dir / f"{self.run_id}.json", state)
                self._last_state = state
                if self.broadcast and (force or event_type != "progress"):
                    send_tasker_broadcast(self.broadcast_action, event_file, self.state_file, event_type, self.run_id)
            except OSError as exc:
                self.last_error = str(exc)

        if notify and self.notify_progress:
            self._maybe_notify(state, force=force or event_type in {"started", "phase_started", "phase_finished", "finished"})

    def _notification_payload(self, status: str, message: str, progress: dict[str, Any] | None) -> dict[str, Any]:
        phase_name = str((self.current_phase or {}).get("name") or self.command or "work")
        human_phase = _human_phase_name(phase_name)
        if status == "running":
            title = f"{human_phase} in progress"
        elif status == "ok":
            title = f"{human_phase} complete"
        elif status == "failed":
            title = f"{human_phase} failed"
        else:
            title = f"{human_phase}: {status}"

        current, total = _progress_current_total(progress)
        progress_text = ""
        percent = None if not progress else progress.get("percent")
        if current is not None and total is not None:
            progress_text = f"{current}/{total}"
            if isinstance(percent, (int, float)):
                progress_text += f" ({percent:.0f}%)"
        elif isinstance(percent, (int, float)):
            progress_text = f"{percent:.0f}%"

        detail = _human_detail(message)
        bits = []
        if self.repo_name:
            bits.append(self.repo_name)
        if progress_text:
            bits.append(progress_text)
        if detail:
            bits.append(detail)
        body = " • ".join(bits) or "Waiting for progress"
        return {"title": title, "text": body, "expanded_title": title, "expanded_text": body, "body": body, "id": self.notification_id}

    def _maybe_notify(self, state: dict[str, Any], *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_notification_at < self.notification_interval_s:
            return
        self._last_notification_at = now
        notification = state.get("notification") or {}
        title = str(notification.get("title") or "devtool")
        body = str(notification.get("text") or notification.get("body") or state.get("message") or state.get("event_type") or "running")
        ongoing = state.get("status") == "running"
        send_progress_notification(notification_id=self.notification_id, title=title, body=body, ongoing=ongoing)


def build_tasker_sink(
    *,
    enabled: bool,
    directory: str,
    command: str,
    repo_root: Path,
    target: str = "",
    broadcast: bool = False,
    broadcast_action: str = DEFAULT_TASKER_ACTION,
    notify_progress: bool = False,
    notification_id: str = DEFAULT_NOTIFICATION_ID,
) -> TaskerProgressSink | None:
    if not enabled and not notify_progress:
        return None
    repo = repo_root.resolve()
    return TaskerProgressSink(
        directory=tasker_dir_from_text(directory),
        command=command,
        repo_root=str(repo),
        repo_name=repo.name,
        target=target,
        enabled=enabled,
        broadcast=broadcast,
        broadcast_action=broadcast_action,
        notify_progress=notify_progress,
        notification_id=notification_id,
    )
