from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .console_utils import resolve_cli_path
from .models import OperationResult

DEFAULT_TASKER_DIR = "/sdcard/Tasker/devtool"
DEFAULT_TASKER_ACTION = "net.mph.devtool.EVENT"
DEFAULT_NOTIFICATION_ID = "7771"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value.strip())
    cleaned = cleaned.strip(".-")
    return cleaned or "event"


def tasker_dir_from_text(path_text: str | None) -> Path:
    return resolve_cli_path(path_text or DEFAULT_TASKER_DIR)


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
    update_interval_s: float = 0.75
    notification_interval_s: float = 2.0
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
    def events_dir(self) -> Path:
        return self.directory / "events"

    @property
    def runs_dir(self) -> Path:
        return self.directory / "runs"

    def started(self, *, message: str = "started") -> None:
        self.emit("started", status="running", message=message, force=True, notify=True)

    def phase_started(self, name: str, *, index: int, total: int, argv: list[str] | None = None) -> None:
        self.current_phase = {
            "name": name,
            "index": index,
            "total": total,
            "argv": argv or [],
            "started_at": utc_now_iso(),
        }
        self.emit("phase_started", status="running", phase=self.current_phase, progress={"percent": None, "completed": 0, "total": None}, force=True, notify=True)

    def progress(self, *, detail: str = "", completed: int | None = None, total: int | None = None, percent: float | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_update_at < self.update_interval_s:
            return
        self._last_update_at = now
        if percent is None and completed is not None and total:
            percent = max(0.0, min(100.0, completed / total * 100.0))
        payload = {
            "percent": percent,
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
            message=f"{result.name}: {status}",
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
            message="finished" if ok else "failed",
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
            "phase": phase if phase is not None else self.current_phase,
            "progress": progress or {},
            "result": result,
            "results": results,
            "totals": totals or {},
            "notification": notification or self._notification_payload(status, message, progress),
            "files": {
                "state": str(self.state_file),
                "latest_event": str(self.latest_event_file),
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
                _atomic_write_json(event_file, event)
                _atomic_write_json(self.runs_dir / f"{self.run_id}.json", state)
                self._last_state = state
                if self.broadcast and (force or event_type != "progress"):
                    send_tasker_broadcast(self.broadcast_action, event_file, self.state_file, event_type, self.run_id)
            except OSError as exc:
                self.last_error = str(exc)

        if notify and self.notify_progress:
            self._maybe_notify(state, force=force or event_type in {"started", "phase_started", "phase_finished", "finished"})

    def _notification_payload(self, status: str, message: str, progress: dict[str, Any] | None) -> dict[str, Any]:
        title_status = "RUN" if status == "running" else status.upper()
        title = f"devtool {title_status}"
        bits = [self.repo_name, self.command]
        phase_name = (self.current_phase or {}).get("name")
        if phase_name:
            bits.append(str(phase_name))
        if message:
            bits.append(message)
        body = " • ".join(bit for bit in bits if bit)
        percent = None if not progress else progress.get("percent")
        if isinstance(percent, (int, float)):
            body += f" • {percent:.0f}%"
        return {"title": title, "body": body, "id": self.notification_id}

    def _maybe_notify(self, state: dict[str, Any], *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_notification_at < self.notification_interval_s:
            return
        self._last_notification_at = now
        notification = state.get("notification") or {}
        title = str(notification.get("title") or "devtool")
        body = str(notification.get("body") or state.get("message") or state.get("event_type") or "running")
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
