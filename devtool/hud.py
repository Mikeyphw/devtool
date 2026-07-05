from __future__ import annotations

import argparse
import contextlib
import html
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .tasker import DEFAULT_NOTIFICATION_ID, DEFAULT_TASKER_DIR, read_active_hud_directory


DEFAULT_HUD_POLL_INTERVAL = 0.10
DEFAULT_RECENT_EVENTS = 8
DEFAULT_HUD_HOST = "127.0.0.1"
DEFAULT_HUD_PORT = 8765


@dataclass(slots=True)
class HudState:
    title: str = "devtool"
    text: str = "waiting for devtool progress"
    expanded_title: str = "devtool"
    expanded_text: str = "waiting for devtool progress"
    current: int | None = None
    total: int | None = None
    status: str = "idle"
    event_type: str = "idle"
    phase_name: str = ""
    command: str = ""
    repo_name: str = ""
    repo_root: str = ""
    target: str = ""
    updated_at: str = ""
    seq: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_progress(self) -> bool:
        return self.current is not None and self.total is not None and self.total > 0

    @property
    def percent(self) -> int | None:
        if not self.has_progress:
            return None
        assert self.current is not None and self.total is not None
        if self.total <= 0:
            return None
        return max(0, min(100, int(round(self.current / self.total * 100))))

    @property
    def display_title(self) -> str:
        if self.percent is None:
            return self.title
        return f"{self.title} ({self.percent}%)"

    @property
    def display_text(self) -> str:
        bits: list[str] = []
        if self.repo_name:
            bits.append(self.repo_name)
        phase = human_phase_name(self.phase_name or self.command) if (self.phase_name or self.command) else ""
        if phase:
            bits.append(phase)
        detail = human_detail(self.text or self.expanded_text)
        if detail and detail.lower() not in {phase.lower(), self.repo_name.lower()} :
            bits.append(detail)
        return " • ".join(bit for bit in bits if bit) or "Waiting for progress"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "text": self.text,
            "expanded_title": self.expanded_title,
            "expanded_text": self.expanded_text,
            "current": self.current,
            "total": self.total,
            "percent": self.percent,
            "status": self.status,
            "event_type": self.event_type,
            "phase_name": self.phase_name,
            "command": self.command,
            "repo_name": self.repo_name,
            "repo_root": self.repo_root,
            "target": self.target,
            "updated_at": self.updated_at,
            "seq": self.seq,
            "display_title": self.display_title,
            "display_text": self.display_text,
            "human_status": human_status(self.status),
            "human_event_type": human_event_type(self.event_type),
            "human_phase_name": human_phase_name(self.phase_name),
        }


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _tail_lines(path: Path, limit: int, *, block_size: int = 8192) -> list[str]:
    if limit <= 0:
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            if end <= 0:
                return []
            chunks: list[bytes] = []
            pos = end
            newlines = 0
            while pos > 0 and newlines <= limit:
                read_size = min(block_size, pos)
                pos -= read_size
                fh.seek(pos)
                chunk = fh.read(read_size)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
            data = b"".join(reversed(chunks))
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


def _last_jsonl_events(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    events: list[dict[str, Any]] = []
    # Read a few extra lines because malformed/partial lines are skipped.
    for line in _tail_lines(path, max(limit * 3, limit)):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events[-limit:]


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def parse_hud_state(payload: dict[str, Any] | None) -> HudState:
    if not payload:
        return HudState()

    # notification.json uses simple top-level fields. latest_event/state.json use
    # a richer envelope. Accept both so HUD survives partial upgrades.
    notification = _first_dict(payload.get("tasker_notification"), payload.get("notification"), payload)
    progress = _first_dict(payload.get("progress"))
    phase = _first_dict(payload.get("phase"))

    current = _coerce_int(payload.get("current"))
    total = _coerce_int(payload.get("total"))
    if current is None:
        current = _coerce_int(notification.get("current"))
    if total is None:
        total = _coerce_int(notification.get("total"))
    if current is None:
        current = _coerce_int(progress.get("current") or progress.get("completed"))
    if total is None:
        total = _coerce_int(progress.get("total"))

    seq = _coerce_int(payload.get("seq") or notification.get("seq"))
    title = str(notification.get("title") or payload.get("title") or "devtool")
    text = str(notification.get("text") or notification.get("body") or payload.get("message") or payload.get("text") or "")
    expanded_title = str(notification.get("expanded_title") or title)
    expanded_text = str(notification.get("expanded_text") or notification.get("body") or text)

    return HudState(
        title=title,
        text=text,
        expanded_title=expanded_title,
        expanded_text=expanded_text,
        current=current,
        total=total,
        status=str(payload.get("status") or "running"),
        event_type=str(payload.get("event_type") or payload.get("type") or "progress"),
        phase_name=str(phase.get("name") or payload.get("phase_name") or ""),
        command=str(payload.get("command") or ""),
        repo_name=str(payload.get("repo_name") or ""),
        repo_root=str(payload.get("repo_root") or ""),
        target=str(payload.get("target") or ""),
        updated_at=str(payload.get("updated_at") or payload.get("seq") or ""),
        seq=seq,
        raw=payload,
    )


def render_ascii_bar(current: int | None, total: int | None, *, width: int = 28) -> str:
    if current is None:
        return "░" * width
    if total is None or total <= 0:
        # Indeterminate but active: show movement/progress without pretending
        # we know the final denominator.
        filled = min(width, max(1, current % (width + 1)))
        return "█" * filled + "░" * (width - filled)
    current = max(0, min(current, total))
    filled = int(round((current / total) * width))
    return "█" * filled + "░" * (width - filled)




def human_phase_name(value: str) -> str:
    key = (value or "").strip().lower().replace("_", "-")
    mapping = {
        "restore": "Restore",
        "build": "Build",
        "test": "Tests",
        "validate": "Validation",
        "apply-overlay": "Apply overlay",
        "apply-snapshot": "Apply snapshot",
        "commit": "Commit",
        "started": "Started",
        "progress": "Progress",
        "phase-started": "Started",
        "phase_started": "Started",
        "phase-finished": "Finished",
        "phase_finished": "Finished",
        "finished": "Finished",
    }
    return mapping.get(key, (value or "devtool").strip().replace("-", " ").replace("_", " ").title())


def human_detail(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    replacements = {
        "waiting for devtool progress": "Waiting for progress",
        "restore: determining projects": "Finding projects to restore",
        "restore: all projects up-to-date": "All projects are already restored",
        "restore completed": "Restore completed",
        "build: msbuild starting": "Starting MSBuild",
        "test: starting execution": "Starting tests",
        "finished": "Finished",
        "failed": "Failed",
        "started": "Started",
    }
    if lowered in replacements:
        return replacements[lowered]
    for prefix, pretty in (("restored ", "Restored "), ("up-to-date ", "Already up to date: "), ("built ", "Built "), ("testing ", "Testing ")):
        if lowered.startswith(prefix):
            remainder = text[len(prefix):]
            if " -> " in remainder:
                remainder = remainder.split(" -> ", 1)[0]
            return pretty + remainder
    if lowered.startswith("tests:"):
        return text[0].upper() + text[1:]
    return text


def human_status(value: str) -> str:
    key = (value or "").strip().lower()
    return {
        "idle": "Waiting",
        "running": "Running",
        "ok": "Complete",
        "failed": "Failed",
        "skipped": "Skipped",
    }.get(key, human_phase_name(value))


def human_event_type(value: str) -> str:
    return human_phase_name(value)



def _coerce_str(value: Any) -> str:
    return str(value or "").strip()


def _progress_pair(event: dict[str, Any]) -> tuple[int | None, int | None]:
    progress = event.get("progress") if isinstance(event.get("progress"), dict) else {}
    current = _coerce_int(event.get("current"))
    total = _coerce_int(event.get("total"))
    if current is None:
        current = _coerce_int(progress.get("current") or progress.get("completed"))
    if total is None:
        total = _coerce_int(progress.get("total"))
    return current, total


def _result_status_from_event(event: dict[str, Any]) -> str:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    return _coerce_str(result.get("status") or event.get("status"))


def _phase_from_event(event: dict[str, Any]) -> str:
    phase = event.get("phase") if isinstance(event.get("phase"), dict) else {}
    return _coerce_str(phase.get("name") or event.get("phase_name") or event.get("command"))


def _event_message(event: dict[str, Any]) -> str:
    progress = event.get("progress") if isinstance(event.get("progress"), dict) else {}
    return _coerce_str(event.get("message") or progress.get("detail") or event.get("text"))


def _is_noisy_test_line(text: str) -> bool:
    lower = text.strip().lower()
    if not lower:
        return False
    return (
        lower.startswith("a total of ")
        or " test files matched the specified pattern" in lower
        or lower.startswith("test run for ")
        or lower.startswith("starting test execution")
    )


def _is_test_summary(text: str) -> bool:
    lower = text.strip().lower()
    return lower.startswith("tests:") and "passed" in lower and "failed" in lower


def _parse_test_summary(text: str) -> str | None:
    # Accept lines like: tests: 6 passed, 0 failed, 0 skipped
    import re
    match = re.search(r"(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped", text, re.I)
    if not match:
        return None
    passed, failed, skipped = map(int, match.groups())
    parts = [f"{passed} passed"]
    if failed:
        parts.append(f"{failed} failed")
    if skipped:
        parts.append(f"{skipped} skipped")
    if failed:
        return "Tests failing: " + ", ".join(parts)
    return "Tests passing: " + ", ".join(parts)


def friendly_event_line(event: dict[str, Any]) -> str | None:
    event_type = _coerce_str(event.get("event_type") or event.get("type") or "event").lower().replace("_", "-")
    phase = human_phase_name(_phase_from_event(event))
    message = _event_message(event)
    detail = human_detail(message)
    current, total = _progress_pair(event)
    status = _result_status_from_event(event).lower()

    # Hide low-value test runner boilerplate in the recent list.
    if event_type == "progress" and _is_noisy_test_line(message):
        return None

    if _is_test_summary(message):
        parsed = _parse_test_summary(message)
        if parsed:
            return parsed

    if event_type == "phase-started":
        return f"Starting {phase}" if phase else "Starting next step"

    if event_type == "phase-finished":
        if status in {"ok", "complete", "completed", "success"}:
            return f"{phase} completed" if phase else "Step completed"
        if status == "failed":
            return f"{phase} failed" if phase else "Step failed"
        if status == "skipped":
            return f"{phase} skipped" if phase else "Step skipped"
        return f"{phase}: {human_status(status)}" if phase else human_status(status)

    if event_type == "finished":
        if status in {"ok", "complete", "completed", "success"}:
            return "Devtool finished successfully"
        if status == "failed":
            return "Devtool finished with errors"
        return "Devtool finished"

    if event_type == "progress":
        if detail:
            # For progress lists, do not repeat 9/9 on every line unless it is the only useful info.
            return detail
        if current is not None and total is not None:
            if current <= total:
                return f"{phase}: {current}/{total}" if phase else f"Progress {current}/{total}"
            return f"{phase}: {current} done" if phase else f"{current} done"
        if current is not None:
            return f"{phase}: {current} done" if phase else f"{current} done"
        return f"{phase} is running" if phase else "Running"

    if detail and detail.lower() not in {event_type, phase.lower()}:
        return detail
    return human_event_type(event_type)



def compact_operation_title(state: HudState) -> str:
    """Return the one-line headline for the compact GTK HUD."""
    phase = human_phase_name(state.phase_name or state.command or state.event_type)
    status = (state.status or "").strip().lower()
    if status == "failed":
        return f"Failed during {phase}" if phase else "Devtool failed"
    if status == "ok":
        return "Devtool finished"
    if phase and phase.lower() not in {"progress", "idle"}:
        return f"Finished {phase}" if state.event_type in {"phase_finished", "phase-finished"} else f"Running {phase}"
    title = human_detail(state.title)
    return title if title and title.lower() != "devtool" else "Devtool is running"


def compact_detail_text(state: HudState) -> str:
    bits: list[str] = []
    detail = human_detail(state.text or state.expanded_text)
    if detail and detail.lower() not in {"devtool", "progress"}:
        bits.append(detail)
    if state.repo_name:
        bits.append(state.repo_name)
    if state.target:
        bits.append(state.target)
    return " • ".join(bits) or "Waiting for progress"


def _phase_highlight_from_event(event: dict[str, Any]) -> tuple[str, str] | None:
    phase_key = _phase_from_event(event).lower().replace("_", "-")
    key = {"rs":"restore", "restore":"restore", "b":"build", "build":"build", "t":"test", "test":"test", "tests":"test"}.get(phase_key)
    if not key:
        return None
    event_type = _coerce_str(event.get("event_type") or event.get("type")).lower().replace("_", "-")
    message = _event_message(event)
    if event_type == "progress" and _is_noisy_test_line(message):
        return None
    if _is_test_summary(message):
        parsed = _parse_test_summary(message)
        if parsed:
            return key, parsed
    detail = human_detail(message)
    if detail and detail.lower() not in {"started", "running", "progress"}:
        return key, detail
    if event_type == "phase-finished":
        status = _result_status_from_event(event).lower()
        if status in {"ok", "complete", "completed", "success"}:
            return key, f"{human_phase_name(key)} completed"
        if status == "failed":
            return key, f"{human_phase_name(key)} failed"
        if status == "skipped":
            return key, f"{human_phase_name(key)} skipped"
    return None


def last_phase_highlights(events: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Return the last useful restore/build/test detail for the HUD."""
    highlights: dict[str, str] = {}
    for event in events:
        parsed = _phase_highlight_from_event(event)
        if parsed is not None:
            highlights[parsed[0]] = parsed[1]
    return highlights


def latest_log_path(events: Iterable[dict[str, Any]]) -> str:
    """Find the newest log_path embedded in Tasker/HUD events."""
    latest = ""
    for event in events:
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if result.get("log_path"):
            latest = str(result.get("log_path"))
        results = event.get("results") if isinstance(event.get("results"), list) else []
        for item in results:
            if isinstance(item, dict) and item.get("log_path"):
                latest = str(item.get("log_path"))
    return latest

def _newest_existing(paths: list[Path]) -> Path | None:
    existing = []
    for path in paths:
        try:
            existing.append((path.stat().st_mtime_ns, path))
        except OSError:
            pass
    if not existing:
        return None
    return max(existing, key=lambda item: item[0])[1]


def resolve_hud_directory(directory: Path, *, follow_active: bool = False) -> Path:
    if not follow_active:
        return directory
    return read_active_hud_directory(directory) or directory


def snapshot_from_runtime_directory(directory: Path, *, follow_active: bool = False, recent_events: int = DEFAULT_RECENT_EVENTS) -> dict[str, Any]:
    current = resolve_hud_directory(directory, follow_active=follow_active)
    snapshot = snapshot_from_directory(current, recent_events=recent_events)
    snapshot["active_directory"] = str(current)
    snapshot["follow_active"] = bool(follow_active)
    return snapshot


def state_from_directory(directory: Path) -> HudState:
    # Keep notification.json as the simple primary state file, but fall back to
    # newer rich state/event files when notification.json is missing or clearly
    # stale.  The stale threshold avoids flicker in tests/tools that write a
    # static notification file and then append historical events immediately.
    notification_path = directory / "notification.json"
    notification = _read_json_file(notification_path)
    if notification is not None:
        try:
            notification_mtime = notification_path.stat().st_mtime_ns
            events_mtime = (directory / "events.jsonl").stat().st_mtime_ns
        except OSError:
            events_mtime = 0
            notification_mtime = 0
        if events_mtime <= notification_mtime + 250_000_000:
            return parse_hud_state(notification)

    candidates = [directory / "latest_event.json", directory / "state.json"]
    newest = _newest_existing(candidates)
    payload = _read_json_file(newest) if newest is not None else notification
    events_path = directory / "events.jsonl"
    try:
        events_newer = newest is None or events_path.stat().st_mtime_ns > newest.stat().st_mtime_ns
    except OSError:
        events_newer = False
    if events_newer:
        events = _last_jsonl_events(events_path, 1)
        if events:
            payload = events[-1]
    return parse_hud_state(payload)


def snapshot_from_directory(directory: Path, *, recent_events: int = DEFAULT_RECENT_EVENTS) -> dict[str, Any]:
    events = _last_jsonl_events(directory / "events.jsonl", recent_events)
    state = state_from_directory(directory)
    if state.raw == {} and events:
        state = parse_hud_state(events[-1])
    return {
        "version": 5,
        "state": state.to_dict(),
        "events": events,
        "paths": {
            "directory": str(directory),
            "notification": str(directory / "notification.json"),
            "events": str(directory / "events.jsonl"),
            "latest_event": str(directory / "latest_event.json"),
            "state": str(directory / "state.json"),
        },
        "generated_at": time.time(),
    }


def _event_summary(event: dict[str, Any]) -> tuple[str, str, Any, Any]:
    event_type = str(event.get("event_type") or event.get("type") or "event")
    progress = event.get("progress") if isinstance(event.get("progress"), dict) else {}
    message = str(event.get("message") or progress.get("detail") or event.get("text") or "")
    current = event.get("current")
    total = event.get("total")
    if current is None:
        current = progress.get("current") or progress.get("completed")
    if total is None:
        total = progress.get("total")
    return event_type, message, current, total


class TerminalHud:
    def __init__(self, *, console: Console | None = None, recent_events: int = DEFAULT_RECENT_EVENTS, title: str = "devtool HUD v3") -> None:
        self.console = console or Console()
        self.recent_events = recent_events
        self.title = title

    def render(self, state: HudState, events: list[dict[str, Any]]) -> Panel:
        table = Table.grid(expand=True)
        table.add_column(ratio=1)
        bar = render_ascii_bar(state.current, state.total, width=34)
        progress_text = "unknown"
        if state.current is not None and state.total is not None:
            progress_text = f"{state.current}/{state.total}"
            if state.percent is not None:
                progress_text += f"  {state.percent}%"
        title = Text(state.display_title, style="bold")
        detail = Text(state.display_text or "waiting")
        table.add_row(title)
        table.add_row(Text(f"{bar}  {progress_text}"))
        table.add_row(detail)
        summary_bits = [human_status(state.status)]
        if state.phase_name:
            summary_bits.append(human_phase_name(state.phase_name))
        elif state.event_type:
            summary_bits.append(human_event_type(state.event_type))
        if state.repo_name:
            summary_bits.append(state.repo_name)
        table.add_row(Text(" • ".join(bit for bit in summary_bits if bit), style="dim"))
        recent = events[-self.recent_events :]
        if recent:
            lines = [line for event in recent if (line := friendly_event_line(event))]
            if lines:
                table.add_row(Text("recent", style="bold dim"))
                for line in lines[-self.recent_events :]:
                    table.add_row(Text(f"• {line}"[:180], style="dim"))
        border_style = "green" if state.status in {"ok", "running", "idle"} else "red"
        return Panel(table, title=self.title, border_style=border_style)

    def run(self, *, directory: Path, interval_s: float, once: bool = False) -> int:
        events_path = directory / "events.jsonl"
        refresh = max(1, min(20, int(1 / max(interval_s, 0.05))))
        with Live(self.render(state_from_directory(directory), _last_jsonl_events(events_path, self.recent_events)), console=self.console, refresh_per_second=refresh) as live:
            while True:
                state = state_from_directory(directory)
                events = _last_jsonl_events(events_path, self.recent_events)
                live.update(self.render(state, events))
                if once:
                    return 0
                time.sleep(interval_s)


class TermuxNotificationHud:
    def __init__(self, *, notification_id: str = DEFAULT_NOTIFICATION_ID) -> None:
        self.notification_id = str(notification_id)
        self.last_signature: tuple[Any, ...] | None = None

    def _build_argv(self, state: HudState) -> list[str]:
        argv = [
            "termux-notification",
            "--id",
            self.notification_id,
            "--title",
            state.display_title,
            "--content",
            state.display_text,
            "--ongoing",
            "--priority",
            "high",
        ]
        # Termux:API builds have not always exposed the same progress argument
        # shape. Keep current/total visible in text by default and allow opt-in
        # to either known argument form via env.
        progress_mode = os.environ.get("DEVTOOL_TERMUX_NOTIFICATION_PROGRESS", "").strip().lower()
        if progress_mode and state.current is not None and state.total is not None:
            current = max(0, min(state.current, state.total)) if state.total > 0 else max(0, state.current)
            total = max(1, state.total or 1)
            if progress_mode in {"csv", "android"}:
                argv += ["--progress", f"{total},{current},false"]
            elif progress_mode in {"split", "legacy"}:
                argv += ["--progress", str(current), "--max", str(total)]
        return argv

    def update(self, state: HudState) -> None:
        signature = (state.display_title, state.display_text, state.current, state.total, state.status)
        if signature == self.last_signature:
            return
        self.last_signature = signature
        if not shutil.which("termux-notification"):
            return
        try:
            subprocess.run(self._build_argv(state), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            return

    def run(self, *, directory: Path, interval_s: float, once: bool = False) -> int:
        while True:
            self.update(state_from_directory(directory))
            if once:
                return 0
            time.sleep(interval_s)


class TermuxGuiHud:
    def __init__(self, *, recent_events: int = DEFAULT_RECENT_EVENTS) -> None:
        self.recent_events = recent_events

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("termuxgui") is not None

    def run(self, *, directory: Path, interval_s: float, once: bool = False) -> int:
        try:
            import termuxgui as tg  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            raise RuntimeError("termuxgui is not installed. Install it with: pip install termuxgui") from None

        events_path = directory / "events.jsonl"
        with tg.Connection() as connection:
            activity = tg.Activity(connection)
            root = tg.LinearLayout(activity)
            title = tg.TextView(activity, "devtool HUD v3", root)
            with contextlib.suppress(Exception):
                title.settextsize(20)
            progress = tg.TextView(activity, "░" * 28, root)
            with contextlib.suppress(Exception):
                progress.settextsize(22)
            counts = tg.TextView(activity, "waiting", root)
            detail = tg.TextView(activity, "waiting for progress", root)
            recent = tg.TextView(activity, "", root)

            while True:
                state = state_from_directory(directory)
                events = _last_jsonl_events(events_path, self.recent_events)
                bar = render_ascii_bar(state.current, state.total, width=26)
                progress_text = "unknown"
                if state.current is not None and state.total is not None:
                    progress_text = f"{state.current}/{state.total}"
                    if state.percent is not None:
                        progress_text += f"  {state.percent}%"
                elif state.current is not None:
                    progress_text = f"{state.current} done"
                title.settext(state.display_title)
                progress.settext(bar)
                counts.settext(progress_text)
                detail.settext(state.display_text)
                recent_lines = []
                for event in events[-self.recent_events :]:
                    event_type, message, current, total = _event_summary(event)
                    if current is not None and total is not None:
                        suffix = f" {current}/{total}"
                    elif current is not None:
                        suffix = f" {current} done"
                    else:
                        suffix = ""
                    recent_lines.append(f"• {human_event_type(event_type)}{suffix} {human_detail(message)}".strip())
                recent.settext("\n".join(recent_lines[-self.recent_events :]))
                if once:
                    return 0
                time.sleep(interval_s)




class DesktopNotificationHud:
    """Unix desktop notification HUD using notify-send/libnotify.

    GNOME Shell can show the text reliably. Some notification daemons also
    honor the `value` hint as a progress bar; when they do not, the title still
    includes current/total and percent.
    """

    def __init__(self, *, notification_id: str = DEFAULT_NOTIFICATION_ID) -> None:
        self.notification_id = str(notification_id)
        self.last_signature: tuple[Any, ...] | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("notify-send") is not None

    def _build_argv(self, state: HudState) -> list[str]:
        percent = state.percent or 0
        argv = [
            "notify-send",
            "--app-name=devtool",
            "--urgency=normal",
            "--expire-time=1000",
            "--hint=string:x-canonical-private-synchronous:devtool-hud",
            f"--hint=int:value:{percent}",
            state.display_title,
            state.display_text,
        ]
        return argv

    def update(self, state: HudState) -> None:
        signature = (state.display_title, state.display_text, state.current, state.total, state.status)
        if signature == self.last_signature:
            return
        self.last_signature = signature
        if not self.available():
            return
        try:
            subprocess.run(self._build_argv(state), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            return

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False) -> int:
        while True:
            current_dir = resolve_hud_directory(directory, follow_active=follow_active)
            self.update(state_from_directory(current_dir))
            if once:
                return 0
            time.sleep(interval_s)


class GtkDesktopHud:
    """Compact GTK floating desktop HUD for GNOME/Unix desktops."""

    def __init__(self, *, recent_events: int = DEFAULT_RECENT_EVENTS) -> None:
        self.recent_events = recent_events

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("gi") is not None and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    def run(self, *, directory: Path, interval_s: float, once: bool = False, follow_active: bool = False) -> int:
        if once:
            snapshot = snapshot_from_runtime_directory(directory, follow_active=follow_active, recent_events=self.recent_events)
            print(json.dumps({"state": snapshot["state"], "events": snapshot["events"], "phase_highlights": last_phase_highlights(snapshot["events"])}, ensure_ascii=False, indent=2))
            return 0
        try:
            import gi  # type: ignore[import-not-found]
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk, GLib, Gtk, Pango  # type: ignore[import-not-found]
        except Exception as exc:
            raise RuntimeError("GTK HUD requires PyGObject/GTK 3. On Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0") from exc

        window = Gtk.Window(title="devtool HUD")
        window.set_default_size(440, 330)
        window.set_keep_above(True)
        window.set_resizable(True)
        window.connect("destroy", Gtk.main_quit)

        css = b"""
        .hud-root { background: #111318; color: #f3f4f6; }
        .hud-header { color: #9ca3af; font-size: 11px; letter-spacing: 0.04em; }
        .hud-title { color: #f9fafb; font-size: 18px; font-weight: 700; }
        .hud-muted { color: #a7b0bd; }
        .hud-meta { color: #d1d5db; font-size: 12px; }
        .hud-event { color: #d1d5db; font-size: 12px; }
        .hud-debug { color: #9ca3af; font-family: monospace; font-size: 11px; }
        .hud-ok { color: #5ee49b; }
        .hud-failed { color: #ff6978; }
        .hud-running { color: #ffd166; }
        button { border-radius: 8px; padding: 5px 9px; }
        """
        provider = Gtk.CssProvider(); provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.get_style_context().add_class("hud-root")
        outer.set_margin_top(12); outer.set_margin_bottom(12); outer.set_margin_start(12); outer.set_margin_end(12)
        window.add(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); outer.pack_start(header, False, False, 0)
        header_label = Gtk.Label(label="devtool HUD Lite"); header_label.set_xalign(0); header_label.get_style_context().add_class("hud-header"); header.pack_start(header_label, True, True, 0)
        percent_label = Gtk.Label(label="● waiting"); percent_label.set_xalign(1); percent_label.get_style_context().add_class("hud-header"); header.pack_end(percent_label, False, False, 0)
        title = Gtk.Label(label="Waiting for progress"); title.set_xalign(0); title.set_line_wrap(True); title.get_style_context().add_class("hud-title"); outer.pack_start(title, False, False, 0)
        detail = Gtk.Label(label="No devtool task has reported progress yet."); detail.set_xalign(0); detail.set_line_wrap(True); detail.get_style_context().add_class("hud-muted"); outer.pack_start(detail, False, False, 0)
        progress = Gtk.ProgressBar(); progress.set_show_text(True); outer.pack_start(progress, False, False, 0)
        meta = Gtk.Label(label="Phase: waiting  ·  Repo: -  ·  Target: -"); meta.set_xalign(0); meta.set_line_wrap(True); meta.get_style_context().add_class("hud-meta"); outer.pack_start(meta, False, False, 0)
        phase_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2); outer.pack_start(phase_box, False, False, 0)
        restore_label = Gtk.Label(label="Restore: -"); build_label = Gtk.Label(label="Build: -"); test_label = Gtk.Label(label="Test: -")
        for label in (restore_label, build_label, test_label):
            label.set_xalign(0); label.set_ellipsize(Pango.EllipsizeMode.END); label.get_style_context().add_class("hud-meta"); phase_box.pack_start(label, False, False, 0)
        events_label = Gtk.Label(label="No events yet"); events_label.set_xalign(0); events_label.set_yalign(0); events_label.set_selectable(True); events_label.set_line_wrap(True); events_label.get_style_context().add_class("hud-event"); outer.pack_start(events_label, True, True, 0)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6); outer.pack_start(actions, False, False, 0)
        copy_button = Gtk.Button(label="Copy summary"); log_button = Gtk.Button(label="Open log"); close_button = Gtk.Button(label="Close HUD")
        actions.pack_start(copy_button, False, False, 0); actions.pack_start(log_button, False, False, 0); actions.pack_end(close_button, False, False, 0)
        debug = Gtk.Expander(label="Debug"); debug.set_expanded(False)
        debug_label = Gtk.Label(label=""); debug_label.set_xalign(0); debug_label.set_yalign(0); debug_label.set_selectable(True); debug_label.set_line_wrap(True); debug_label.get_style_context().add_class("hud-debug"); debug.add(debug_label); outer.pack_start(debug, False, False, 0)
        last_summary = {"text": "devtool HUD Lite: waiting", "log_path": ""}

        def _set_status_classes(widget: Any, status: str) -> None:
            ctx = widget.get_style_context()
            for cls in ("hud-ok", "hud-failed", "hud-running"):
                ctx.remove_class(cls)
            ctx.add_class("hud-failed" if status == "failed" else "hud-ok" if status == "ok" else "hud-running")
        def _copy_summary(_button: Any) -> None:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD); clipboard.set_text(str(last_summary.get("text") or ""), -1); clipboard.store()
        def _open_log(_button: Any) -> None:
            log_path = str(last_summary.get("log_path") or "")
            if log_path:
                with contextlib.suppress(Exception):
                    subprocess.Popen(["xdg-open", log_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        copy_button.connect("clicked", _copy_summary); log_button.connect("clicked", _open_log); close_button.connect("clicked", lambda _button: Gtk.main_quit())

        def refresh() -> bool:
            current_dir = resolve_hud_directory(directory, follow_active=follow_active)
            snapshot = snapshot_from_directory(current_dir, recent_events=max(self.recent_events, 12))
            state = state_from_directory(current_dir)
            events = snapshot.get("events") if isinstance(snapshot.get("events"), list) else []
            highlights = last_phase_highlights(events)
            operation = compact_operation_title(state)
            title.set_text(operation); detail.set_text(compact_detail_text(state))
            percent_text = f"● {human_status(state.status).lower()}" + (f"  {state.percent}%" if state.percent is not None else "")
            percent_label.set_text(percent_text); _set_status_classes(percent_label, state.status)
            if state.current is not None and state.total is not None and state.total > 0:
                progress.set_fraction(max(0.0, min(1.0, state.current / state.total))); progress.set_text(f"{state.current}/{state.total}  {state.percent or 0}%")
            elif state.current is not None:
                progress.pulse(); progress.set_text(f"{state.current} done")
            else:
                progress.pulse(); progress.set_text("waiting")
            meta_bits = []
            if state.phase_name: meta_bits.append(f"Phase: {human_phase_name(state.phase_name)}")
            elif state.command: meta_bits.append(f"Command: {human_phase_name(state.command)}")
            if state.repo_name: meta_bits.append(f"Repo: {state.repo_name}")
            if state.target: meta_bits.append(f"Target: {state.target}")
            if follow_active: meta_bits.append("following active HUD")
            meta.set_text("  ·  ".join(meta_bits) or "Waiting")
            restore_label.set_text("Restore: " + highlights.get("restore", "-")); build_label.set_text("Build: " + highlights.get("build", "-")); test_label.set_text("Test: " + highlights.get("test", "-"))
            lines = [line for event in events if (line := friendly_event_line(event))]
            shown = lines[-5:]
            events_label.set_text("\n".join(f"• {line}" for line in shown) or "No events yet")
            log_path = latest_log_path(events); log_button.set_sensitive(bool(log_path) and shutil.which("xdg-open") is not None)
            summary_lines = [operation, compact_detail_text(state), meta.get_text(), restore_label.get_text(), build_label.get_text(), test_label.get_text(), *shown]
            last_summary["text"] = "\n".join(line for line in summary_lines if line); last_summary["log_path"] = log_path or ""
            debug_label.set_text("\n".join([f"active_dir={current_dir}", f"state={current_dir / 'state.json'}", f"events={current_dir / 'events.jsonl'}", f"seq={state.seq if state.seq is not None else '-'} status={state.status} event={state.event_type}", f"command={state.command or '-'} phase={state.phase_name or '-'}", f"log={log_path or '-'}"]))
            return True
        GLib.timeout_add(max(100, int(interval_s * 1000)), refresh); refresh(); window.show_all(); Gtk.main(); return 0


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>devtool HUD v5</title>
<style>
:root{color-scheme:dark;--bg:#0b0f14;--card:#121923;--muted:#8ea0b4;--text:#e8eef6;--line:#243244;--ok:#5ee49b;--bad:#ff6978;--warn:#ffd166}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:16px}.wrap{max-width:980px;margin:0 auto;display:grid;gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px;box-shadow:0 16px 40px rgba(0,0,0,.25)}.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.title{font-size:22px;font-weight:800}.pill{font-size:12px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:4px 9px}.bar{height:18px;background:#071018;border:1px solid var(--line);border-radius:999px;overflow:hidden}.fill{height:100%;width:0;background:linear-gradient(90deg,var(--ok),#68d8ff);transition:width .18s ease}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.kv{background:#0c131c;border:1px solid var(--line);border-radius:14px;padding:10px}.k{color:var(--muted);font-size:12px}.v{font-weight:700;overflow-wrap:anywhere}.events{display:grid;gap:8px;max-height:50vh;overflow:auto}.event{border:1px solid var(--line);border-radius:14px;padding:10px;background:#0c131c}.event .meta{color:var(--muted);font-size:12px;margin-bottom:4px}pre{white-space:pre-wrap;word-break:break-word;margin:0;color:#cfe3ff}.bad{color:var(--bad)}.ok{color:var(--ok)}@media(max-width:720px){body{padding:10px}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.title{font-size:18px}}
</style>
</head>
<body>
<div class="wrap">
  <section class="card top"><div><div class="title" id="title">devtool HUD</div><div id="text" class="pill">waiting</div></div><div class="pill" id="connected">connecting</div></section>
  <section class="card"><div class="bar"><div class="fill" id="fill"></div></div><p><b id="pct">0%</b> <span id="count" class="pill">-/-</span></p></section>
  <section class="grid"><div class="kv"><div class="k">State</div><div class="v" id="status">idle</div></div><div class="kv"><div class="k">Update</div><div class="v" id="event">idle</div></div><div class="kv"><div class="k">Phase</div><div class="v" id="phase">-</div></div><div class="kv"><div class="k">Project</div><div class="v" id="repo">-</div></div></section>
  <section class="card"><h3>Recent events</h3><div id="events" class="events"></div></section>
</div>
<script>
const $=id=>document.getElementById(id);
function esc(s){return String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function eventMsg(e){const p=e.progress||{}; return e.message || p.detail || e.text || '';}
function render(data){const s=data.state||{}; const pct=s.percent ?? 0; $('title').textContent=s.display_title||s.title||'devtool HUD'; $('text').textContent=s.display_text||s.text||'waiting'; $('fill').style.width=Math.max(0,Math.min(100,pct))+'%'; $('pct').textContent=pct+'%'; $('count').textContent=(s.current ?? '-')+'/'+(s.total ?? '-'); $('status').textContent=s.human_status||s.status||'Waiting'; $('event').textContent=s.human_event_type||s.event_type||'Update'; $('phase').textContent=s.human_phase_name||s.phase_name||'-'; $('repo').textContent=s.repo_name||'-'; const events=(data.events||[]).slice().reverse(); $('events').innerHTML=events.map(e=>`<div class="event"><div class="meta">${esc(e.event_type||e.type||'event')} ${esc(e.current??((e.progress||{}).completed)??'')}/${esc(e.total??((e.progress||{}).total)??'')}</div><pre>${esc(eventMsg(e))}</pre></div>`).join('')||'<div class="pill">No events yet</div>';}
async function poll(){try{const r=await fetch('/api/state',{cache:'no-store'}); render(await r.json()); $('connected').textContent='connected'; $('connected').className='pill ok';}catch(e){$('connected').textContent='offline'; $('connected').className='pill bad';}}
if (!!window.EventSource){const es=new EventSource('/stream'); es.onmessage=ev=>{render(JSON.parse(ev.data)); $('connected').textContent='live'; $('connected').className='pill ok';}; es.onerror=()=>{ $('connected').textContent='reconnecting'; $('connected').className='pill';};} else {setInterval(poll,500);} poll();
</script>
</body>
</html>
"""


class WebDashboardHud:
    def __init__(self, *, host: str = DEFAULT_HUD_HOST, port: int = DEFAULT_HUD_PORT, recent_events: int = DEFAULT_RECENT_EVENTS, interval_s: float = DEFAULT_HUD_POLL_INTERVAL, follow_active: bool = False) -> None:
        self.host = host
        self.port = int(port)
        self.recent_events = recent_events
        self.interval_s = interval_s
        self.follow_active = follow_active
        self.httpd: ThreadingHTTPServer | None = None

    @staticmethod
    def _json_response(handler: BaseHTTPRequestHandler, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _make_handler(self, directory: Path) -> type[BaseHTTPRequestHandler]:
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "devtool-hud/5"

            def log_message(self, fmt: str, *args: Any) -> None:
                if os.environ.get("DEVTOOL_HUD_HTTP_LOG"):
                    super().log_message(fmt, *args)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                path = self.path.split("?", 1)[0]
                if path in {"/", "/index.html"}:
                    data = DASHBOARD_HTML.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path in {"/api/state", "/state.json"}:
                    WebDashboardHud._json_response(self, snapshot_from_runtime_directory(directory, follow_active=dashboard.follow_active, recent_events=dashboard.recent_events))
                    return
                if path in {"/api/events"}:
                    WebDashboardHud._json_response(self, _last_jsonl_events(resolve_hud_directory(directory, follow_active=dashboard.follow_active) / "events.jsonl", dashboard.recent_events))
                    return
                if path == "/events.jsonl":
                    text = "\n".join(_tail_lines(resolve_hud_directory(directory, follow_active=dashboard.follow_active) / "events.jsonl", dashboard.recent_events)) + "\n"
                    data = text.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/health":
                    WebDashboardHud._json_response(self, {"ok": True})
                    return
                if path == "/stream":
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    last_signature = ""
                    while True:
                        snapshot = snapshot_from_runtime_directory(directory, follow_active=dashboard.follow_active, recent_events=dashboard.recent_events)
                        signature = json.dumps(snapshot.get("state", {}), sort_keys=True, separators=(",", ":"))
                        if signature != last_signature:
                            payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                            try:
                                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                return
                            last_signature = signature
                        time.sleep(max(0.1, dashboard.interval_s))
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

        return Handler

    def serve_forever(self, *, directory: Path) -> None:
        handler = self._make_handler(directory)
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.serve_forever(poll_interval=max(0.1, self.interval_s))

    def start_background(self, *, directory: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
        handler = self._make_handler(directory)
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd = httpd
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": max(0.1, self.interval_s)}, daemon=True)
        thread.start()
        return httpd, thread

    def run(self, *, directory: Path, once: bool = False, console: Console | None = None) -> int:
        console = console or Console()
        if once:
            snapshot = snapshot_from_directory(directory, recent_events=self.recent_events)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return 0
        console.print(f"[green]devtool HUD v4/v5 web dashboard[/green] listening on http://{self.host}:{self.port}")
        self.serve_forever(directory=directory)
        return 0


class StudioHud:
    """HUD v5: web dashboard + local console + optional notification mirroring."""

    def __init__(self, *, host: str, port: int, recent_events: int, notification_id: str, notify: bool) -> None:
        self.host = host
        self.port = port
        self.recent_events = recent_events
        self.notification_id = notification_id
        self.notify = notify

    def run(self, *, directory: Path, interval_s: float, once: bool = False, console: Console | None = None, follow_active: bool = False) -> int:
        console = console or Console()
        web = WebDashboardHud(host=self.host, port=self.port, recent_events=self.recent_events, interval_s=interval_s, follow_active=follow_active)
        if once:
            snapshot = snapshot_from_runtime_directory(directory, follow_active=follow_active, recent_events=self.recent_events)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return 0
        httpd, _thread = web.start_background(directory=directory)
        host, port = httpd.server_address[:2]
        console.print(f"[green]devtool HUD v5 studio[/green] web=http://{host}:{port} dir={directory}")
        notifier = TermuxNotificationHud(notification_id=self.notification_id) if self.notify else None
        terminal = TerminalHud(console=console, recent_events=self.recent_events, title="devtool HUD v5 studio")
        refresh = max(1, min(20, int(1 / max(interval_s, 0.05))))
        current_dir = resolve_hud_directory(directory, follow_active=follow_active)
        with Live(terminal.render(state_from_directory(current_dir), _last_jsonl_events(current_dir / "events.jsonl", self.recent_events)), console=console, refresh_per_second=refresh) as live:
            while True:
                current_dir = resolve_hud_directory(directory, follow_active=follow_active)
                state = state_from_directory(current_dir)
                events = _last_jsonl_events(current_dir / "events.jsonl", self.recent_events)
                live.update(terminal.render(state, events))
                if notifier is not None:
                    notifier.update(state)
                time.sleep(interval_s)


def choose_hud_mode(requested: str) -> str:
    requested = requested.lower()
    aliases = {
        "v3": "terminal",
        "console": "terminal",
        "rich": "terminal",
        "v4": "web",
        "server": "web",
        "http": "web",
        "dashboard": "web",
        "v5": "studio",
        "all": "studio",
        "v6": "desktop",
        "unix": "desktop",
        "gnome": "desktop",
        "desktop-gtk": "desktop-window",
        "gtk": "desktop-window",
        "notify-send": "desktop-notify",
        "libnotify": "desktop-notify",
    }
    requested = aliases.get(requested, requested)
    if requested == "desktop":
        if GtkDesktopHud.available():
            return "desktop-window"
        if DesktopNotificationHud.available():
            return "desktop-notify"
        return "terminal"
    if requested != "auto":
        return requested
    if TermuxGuiHud.available():
        return "termux-gui"
    if shutil.which("termux-notification"):
        return "notification"
    if os.name == "posix" and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        if GtkDesktopHud.available():
            return "desktop-window"
        if DesktopNotificationHud.available():
            return "desktop-notify"
    return "terminal"


def build_hud_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devtool hud")
    parser.add_argument("--tasker-dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool HUD/Tasker JSON files (default: platform state dir)")
    parser.add_argument(
        "--mode",
        choices=["auto", "termux-gui", "terminal", "notification", "web", "studio", "desktop", "desktop-window", "desktop-notify", "v3", "v4", "v5", "v6", "console", "dashboard", "all", "unix", "gnome"],
        default="auto",
        help="HUD renderer: terminal/v3, web/v4, studio/v5, or Unix desktop/v6",
    )
    parser.add_argument("--host", default=DEFAULT_HUD_HOST, help="Host/IP for HUD web/studio modes")
    parser.add_argument("--port", type=int, default=DEFAULT_HUD_PORT, help="Port for HUD web/studio modes")
    parser.add_argument("--poll-interval", type=float, default=0.1, help="Seconds between HUD refreshes")
    parser.add_argument("--recent-events", type=int, default=DEFAULT_RECENT_EVENTS, help="Recent JSONL events to show/serve")
    parser.add_argument("--notify-id", default=DEFAULT_NOTIFICATION_ID, help="Notification id for notification/studio modes")
    parser.add_argument("--studio-notify", action="store_true", help="In studio/v5 mode, also mirror state into termux-notification when available")
    parser.add_argument("--once", action="store_true", help="Render/update once and exit")
    parser.add_argument("--follow-active", action="store_true", help="Follow the global active HUD directory written by auto-HUD")
    return parser


def run_hud(args: argparse.Namespace) -> int:
    directory = Path(args.tasker_dir).expanduser()
    mode = choose_hud_mode(str(args.mode))
    interval_s = max(0.05, float(args.poll_interval))
    recent_events = max(0, int(args.recent_events))
    if mode == "termux-gui":
        return TermuxGuiHud(recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once))
    if mode == "notification":
        return TermuxNotificationHud(notification_id=str(args.notify_id)).run(directory=directory, interval_s=interval_s, once=bool(args.once))
    follow_active = bool(getattr(args, "follow_active", False))
    if mode == "web":
        return WebDashboardHud(host=str(args.host), port=int(args.port), recent_events=recent_events, interval_s=interval_s, follow_active=follow_active).run(directory=directory, once=bool(args.once))
    if mode == "studio":
        return StudioHud(host=str(args.host), port=int(args.port), recent_events=recent_events, notification_id=str(args.notify_id), notify=bool(args.studio_notify)).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    if mode == "desktop-window":
        return GtkDesktopHud(recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    if mode == "desktop-notify":
        return DesktopNotificationHud(notification_id=str(args.notify_id)).run(directory=directory, interval_s=interval_s, once=bool(args.once), follow_active=follow_active)
    return TerminalHud(recent_events=recent_events).run(directory=directory, interval_s=interval_s, once=bool(args.once))


def main(argv: list[str] | None = None) -> int:
    parser = build_hud_parser()
    args = parser.parse_args(argv)
    return run_hud(args)


if __name__ == "__main__":
    raise SystemExit(main())
